# -*- coding: utf-8; -*-
"""
Copyright (c) 2018 Rolf Hempel, rolf6419@gmx.de

This file is part of the PlanetarySystemStacker tool (PSS).
https://github.com/Rolf-Hempel/PlanetarySystemStacker

PSS is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with PSS.  If not, see <http://www.gnu.org/licenses/>.

"""

from glob import glob
from math import ceil
from statistics import median
from time import sleep
from warnings import filterwarnings

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from cv2 import FONT_HERSHEY_SIMPLEX, putText, resize, INTER_CUBIC, INTER_LINEAR
from numpy import zeros, full, empty, float32, newaxis, arange, count_nonzero, \
    sqrt, uint16, clip, minimum, mean
from skimage import img_as_uint, img_as_ubyte

from align_frames import AlignFrames
from alignment_points import AlignmentPoints
from configuration import Configuration
from exceptions import InternalError, NotSupportedError, Error
from frames import Frames
from miscellaneous import Miscellaneous
from rank_frames import RankFrames
from timer import timer


class StackFrames(object):
    """
        For every frame de-warp the quality areas selected for stacking. Then stack all the
        de-warped frame sections into a single image.

    """

    def __init__(self, configuration, frames, rank_frames, align_frames, alignment_points, my_timer,
                 progress_signal=None, debug=False, create_image_window_signal=None,
                 update_image_window_signal=None, terminate_image_window_signal=None):
        """
        Initialze the StackFrames object. In particular, allocate empty numpy arrays used in the
        stacking process for buffering and the final stacked image. The size of all those objects
         in y and x directions is equal to the intersection of all frames.

        :param configuration: Configuration object with parameters
        :param frames: Frames object with all video frames
        :param rank_frames: RankFrames object with global quality ranks (between 0. and 1.,
                            1. being optimal) for all frames
        :param align_frames: AlignFrames object with global shift information for all frames
        :param alignment_points: AlignmentPoints object with information of all alignment points
        :param my_timer: Timer object for accumulating times spent in specific code sections
        :param progress_signal: Either None (no progress signalling), or a signal with the signature
                                (str, int) with the current activity (str) and the progress in
                                percent (int).
        """

        # Suppress warnings about precision loss in skimage file format conversions.
        filterwarnings("ignore", category=UserWarning)

        self.configuration = configuration
        self.frames = frames
        self.rank_frames = rank_frames
        self.align_frames = align_frames
        self.alignment_points = alignment_points
        self.my_timer = my_timer
        self.progress_signal = progress_signal
        self.signal_step_size = max(int(self.frames.number / 10), 1)
        self.shift_distribution = None

        # Set a flag which indicates if drizzling is active.
        self.drizzle = self.configuration.drizzle_factor != 1

        for name in ['Stacking: AP initialization', 'Stacking: initialize background blending',
                     'Stacking: compute AP shifts', 'Stacking: remapping and adding',
                     'Stacking: computing background', 'Stacking: merging AP buffers']:
            self.my_timer.create_no_check(name)

        self.my_timer.start('Stacking: AP initialization')
        # Allocate work space for image buffer and the image converted for output.
        # [dim_y_drizzled, dim_x_drizzled] is the size of the intersection of all frames in drizzled
        # coordinates.
        self.dim_y = (self.align_frames.intersection_shape[0][1] -
                      self.align_frames.intersection_shape[0][0])
        self.dim_y_drizzled = self.dim_y * self.configuration.drizzle_factor
        self.dim_x = (self.align_frames.intersection_shape[1][1] -
                      self.align_frames.intersection_shape[1][0])
        self.dim_x_drizzled = self.dim_x * self.configuration.drizzle_factor
        self.number_pixels = self.dim_y * self.dim_x
        self.number_pixels_drizzled = self.dim_y_drizzled * self.dim_x_drizzled

        # Allocate AP stacking buffers and compute drizzled patch index bounds.
        for ap in self.alignment_points.alignment_points:
            AlignmentPoints.initialize_ap_stacking_buffer(ap, self.configuration.drizzle_factor,
                                             self.frames.color)

        # The summation buffer needs to accommodate three color channels in the case of color
        # images. The size is extended if drizzling is active. In this case also allocate a buffer
        # where the interpolated frames are stored.
        if self.frames.color:
            self.stacked_image_buffer = zeros([self.dim_y_drizzled, self.dim_x_drizzled, 3],
                                              dtype=float32)
            if self.drizzle:
                self.frame_drizzled = zeros([self.dim_y_drizzled, self.dim_x_drizzled, 3],
                                                  dtype=float32)
        else:
            self.stacked_image_buffer = zeros([self.dim_y_drizzled, self.dim_x_drizzled],
                                              dtype=float32)
            if self.drizzle:
                self.frame_drizzled = zeros([self.dim_y_drizzled, self.dim_x_drizzled],
                                                  dtype=float32)

        # If the alignment point patches do not cover the entire frame, a background image must
        # be computed and blended in. At this point it is not yet clear if this is necessary.
        self.background_patches = None
        self.averaged_background = None

        # Allocate a buffer which for each pixel of the image accumulates the weights at each pixel.
        # This buffer is used to normalize the image buffer. It is initialized with a small value to
        # avoid divide by zero.
        self.sum_single_frame_weights = full([self.dim_y_drizzled, self.dim_x_drizzled], 1.e-30,
                                             dtype=float32)

        # Prepare for debugging the local de-warping: In each frame a shifted AP patch can be
        # compared to the corresponding section of the reference frame. This is visualized in a
        # separate GUI window. Visualization control is done via three signals passed from the
        # workflow thread.
        self.debug = debug
        self.scale_factor = 3
        self.border = 2
        self.image_delay = 0.5
        self.create_image_window_signal = create_image_window_signal
        self.update_image_window_signal = update_image_window_signal
        self.terminate_image_window_signal = terminate_image_window_signal

        self.my_timer.stop('Stacking: AP initialization')

    def prepare_for_stack_blending(self):
        """
        Find image locations where the background image is needed for stacking. If the fraction of
        such pixels is above a given parameter, a full background image is constructed from the best
        frames during the stacking process. If the fraction is low, the image is subdivided into
        quadratic patches. To save computing time, the background image is constructed only in those
        patches which contain at least one pixel where the background is needed.

        :return:
        """

        self.my_timer.start('Stacking: initialize background blending')

        # The stack size is the number of frames which contribute to each AP stack.
        single_stack_size_float = float(self.alignment_points.stack_size)

        # Add the contributions of all alignment points into a single buffer.
        for alignment_point in self.alignment_points.alignment_points:
            patch_y_low_drizzled = alignment_point['patch_y_low_drizzled']
            patch_y_high_drizzled = alignment_point['patch_y_high_drizzled']
            patch_x_low_drizzled = alignment_point['patch_x_low_drizzled']
            patch_x_high_drizzled = alignment_point['patch_x_high_drizzled']

            # If the patch is on the image boundary, do not ramp down the weight from the patch
            # center towards that boundary. This way it is avoided that the background image is
            # computed there and blended in with the patch.
            extend_low_y = patch_y_low_drizzled == 0
            extend_high_y = patch_y_high_drizzled == self.dim_y_drizzled
            extend_low_x = patch_x_low_drizzled == 0
            extend_high_x = patch_x_high_drizzled == self.dim_x_drizzled

            # Compute the weights used in AP blending and store them with the AP.
            alignment_point['weights_yx'] = minimum(self.one_dim_weight(patch_y_low_drizzled,
                                                                        patch_y_high_drizzled,
                                                                        alignment_point['y_drizzled'],
                                                                        extend_low=extend_low_y,
                                                                        extend_high=extend_high_y)[
                                                                                    :, newaxis],
                                                    self.one_dim_weight(patch_x_low_drizzled,
                                                                        patch_x_high_drizzled,
                                                                        alignment_point['x_drizzled'],
                                                                        extend_low=extend_low_x,
                                                                        extend_high=extend_high_x)[
                                                                                    newaxis, :])

            # This is an alternative where the weights decrease more rapidly towards the corners.
            # alignment_point['weights_yx'] = self.one_dim_weight(patch_y_low, patch_y_high,
            #                                     alignment_point['y'], extend_low=extend_low_y,
            #                                     extend_high=extend_high_y)[:, newaxis] * \
            #                                 self.one_dim_weight(patch_x_low, patch_x_high,
            #                                     alignment_point['x'], extend_low=extend_low_x,
            #                                     extend_high=extend_high_x)

            # For each image buffer pixel add the weights. This is used for normalization later.
            self.sum_single_frame_weights[patch_y_low_drizzled:patch_y_high_drizzled,
            patch_x_low_drizzled: patch_x_high_drizzled] += single_stack_size_float \
                                                            * alignment_point['weights_yx']

        # Compute the fraction of pixels where no AP patch contributes.
        self.number_stacking_holes = count_nonzero(self.sum_single_frame_weights < 1.e-10)

        # If all pixels are covered by AP patches, no background image is required.
        if self.number_stacking_holes == 0:
            self.my_timer.stop('Stacking: initialize background blending')
            return

        # If the alignment points do not cover the full frame, blend the AP contributions with
        # a background computed as the average of globally shifted best frames. The background
        # should only shine through outside AP patches.
        #
        # The "real" alignment point contributions are collected in the "stacked_image_buffer".
        # Only the "holes" in the buffer have to be filled with the "averaged_background".
        # Allocate a buffer for the background image.
        if self.frames.color:
            self.averaged_background = zeros([self.dim_y, self.dim_x, 3], dtype=float32)
        else:
            self.averaged_background = zeros([self.dim_y, self.dim_x], dtype=float32)

        # Compute the number of points where the background image will be used in patch blending.
        points_where_background_used = count_nonzero(self.sum_single_frame_weights <
                                     self.configuration.stack_frames_background_blend_threshold *
                                     single_stack_size_float)

        # If the fraction is below a certain limit, it is worthwhile to compute the background
        # image only where it is needed. Construct a list with patches where the background is
        # needed.
        if points_where_background_used/self.number_pixels_drizzled < \
                self.configuration.stack_frames_background_fraction:

            # Initialize a list of background patches.
            self.background_patches = []

            # Subdivide the image area in quadratic patches. Cycle through all patch locations.
            for patch_y_low in range(0, self.dim_y,
                                     self.configuration.stack_frames_background_patch_size):
                patch_y_high = min(
                    patch_y_low + self.configuration.stack_frames_background_patch_size,
                    self.dim_y - 1)

                # Handle the special case where the patch has zero size in one direction.
                if patch_y_low == patch_y_high:
                    continue
                for patch_x_low in range(0, self.dim_x,
                                         self.configuration.stack_frames_background_patch_size):
                    patch_x_high = min(
                        patch_x_low + self.configuration.stack_frames_background_patch_size,
                        self.dim_x - 1)
                    if patch_x_low == patch_x_high:
                        continue

                    # If the patch contains pixels where the background is used, add it to the list.
                    if count_nonzero(self.sum_single_frame_weights[
                                     patch_y_low * self.configuration.drizzle_factor:
                                     patch_y_high * self.configuration.drizzle_factor,
                                     patch_x_low * self.configuration.drizzle_factor:
                                     patch_x_high * self.configuration.drizzle_factor] <
                                     self.configuration.stack_frames_background_blend_threshold *
                                     single_stack_size_float) > 0:
                        background_patch = {}
                        background_patch['patch_y_low'] = patch_y_low
                        background_patch['patch_y_high'] = patch_y_high
                        background_patch['patch_x_low'] = patch_x_low
                        background_patch['patch_x_high'] = patch_x_high
                        self.background_patches.append(background_patch)

        self.my_timer.stop('Stacking: initialize background blending')

    def stack_frames(self):
        """
        Compute the shifted contributions of all frames to all alignment points and add them to the
        appropriate alignment point stacking buffers.

        :return: -
        """

        # First find out if there are holes between AP patches.
        self.prepare_for_stack_blending()

        # Initialize the array for shift distribution statistics.
        self.shift_distribution = full((
            self.configuration.alignment_points_search_width *
            self.configuration.drizzle_factor * 2,), 0, dtype=int)
        self.shift_failure_counter = 0

        # If multi-level correlation AP matching is selected, prepare frame-independent data
        # structures used by this particular search algorithm.
        if self.configuration.alignment_points_method == 'MultiLevelCorrelation':
            # Set the two-level reference boxes for all APs.
            self.alignment_points.set_reference_boxes_correlation()

            # Compute the "weight matrix" used in the first correlation phase. It penalizes search
            # results far away from the center.
            search_width_second_phase = 4
            max_search_width_first_phase = int(
                (self.configuration.alignment_points_search_width - search_width_second_phase) / 2)
            search_width_first_phase = max_search_width_first_phase
            extent = 2 * search_width_first_phase + 1
            weight_matrix_first_phase = empty((extent, extent), dtype=float32)
            for y in range(extent):
                for x in range(extent):
                    weight_matrix_first_phase[
                        y, x] = 1. - self.configuration.alignment_points_penalty_factor * (
                            (y / search_width_first_phase - 1) ** 2 + (
                            x / search_width_first_phase - 1) ** 2)

        else:
            weight_matrix_first_phase = None

        # In debug mode: Prepare for de-warp visualization.
        if self.debug:
            self.create_image_window_signal.emit()

        # If brightness normalization is switched on, prepare for adjusting frame brightness.
        if self.configuration.frames_normalization:
            median_brightness = median([self.frames.average_brightness(index)
                                        for index in range(self.frames.number)])
            # print ("min: " + str(min(self.frames.frames_average_brightness)) + ", median: "
            #        + str(median_brightness) + ", max: "
            #        + str(max(self.frames.frames_average_brightness)))

        # Initialize widths of border areas where artifacts occur because not all patches contribute.
        self.border_y_low = self.border_y_high = self.border_x_low = self.border_x_high = 0

        # Go through the list of all frames.
        for frame_index in range(self.frames.number):

            # If brightness normalization is switched on, change the brightness of this frame to
            # the median of all frames.
            if self.configuration.frames_normalization:
                frame = self.frames.frames(frame_index) * median_brightness / \
                        (self.frames.average_brightness(frame_index) + 1.e-7)
            else:
                frame = self.frames.frames(frame_index)

            # Change the current frame into float32. If drizzle is active, also interpolate values.
            if self.drizzle:
                self.frame_drizzled = resize(frame.astype(float32),
                                             (frame.shape[1]*self.configuration.drizzle_factor, frame.shape[0]*self.configuration.drizzle_factor),
                                             interpolation=INTER_LINEAR)
            else:
                self.frame_drizzled = frame.astype(float32)

            frame_mono_blurred = self.frames.frames_mono_blurred(frame_index)

            # After every "signal_step_size"th frame, send a progress signal to the main GUI.
            if self.progress_signal is not None and frame_index % self.signal_step_size == 1:
                self.progress_signal.emit("Stack frames",
                                          int(round(10 * frame_index / self.frames.number) * 10))

            # Look up the constant shifts of the given frame with respect to the mean frame.
            dy = self.align_frames.dy[frame_index]
            dx = self.align_frames.dx[frame_index]

            # Go through all alignment points for which this frame was found to be among the best.
            for alignment_point_index in self.frames.used_alignment_points[frame_index]:
                alignment_point = self.alignment_points.alignment_points[alignment_point_index]

                # Compute the local warp shift for this frame.
                self.my_timer.start('Stacking: compute AP shifts')
                [shift_y, shift_x], success = self.alignment_points.compute_shift_alignment_point(
                    frame_mono_blurred, frame_index, alignment_point_index,
                    de_warp=self.configuration.alignment_points_de_warp,
                    weight_matrix_first_phase=weight_matrix_first_phase,
                    subpixel_solve=self.drizzle)

                # The total shift consists of three components: different coordinate origins for
                # current frame and mean frame, global shift of current frame, and the local warp
                # shift at this alignment point. The first two components are accounted for by dy,
                # dx. If drizzle is active, translate shifts into drizzled pixel distances.
                shift_y_drizzled = int(round(shift_y * self.configuration.drizzle_factor))
                shift_x_drizzled = int(round(shift_x * self.configuration.drizzle_factor))
                total_shift_y_drizzled = dy * self.configuration.drizzle_factor - shift_y_drizzled
                total_shift_x_drizzled = dx * self.configuration.drizzle_factor - shift_x_drizzled

                # Increment the counter corresponding to the 2D warp shift. Increase the resolution
                # according to the drizzle factor.
                if success:
                    self.shift_distribution[int(round(sqrt(shift_y_drizzled ** 2 + shift_x_drizzled ** 2)))] += 1
                else:
                    self.shift_failure_counter += 1

                self.my_timer.stop('Stacking: compute AP shifts')

                # In debug mode: visualize shifted patch of the first AP and compare it with the
                # corresponding patch of the reference frame.
                if self.debug and not alignment_point_index:
                    frame_mono_blurred = self.frames.frames_mono_blurred(frame_index)
                    total_shift_y = dy - shift_y
                    total_shift_y_int = int(round(total_shift_y))
                    total_shift_x = dx - shift_x
                    total_shift_x_int = int(round(total_shift_x))
                    y_low = alignment_point['patch_y_low']
                    y_high = alignment_point['patch_y_high']
                    x_low = alignment_point['patch_x_low']
                    x_high = alignment_point['patch_x_high']
                    reference_patch = (self.alignment_points.mean_frame[y_low:y_high, x_low:x_high]).astype(uint16)
                    reference_patch = resize(reference_patch, None,
                                              fx=float(self.scale_factor),
                                              fy=float(self.scale_factor))

                    try:
                        # Cut out the globally stabilized and the de-warped patches
                        frame_stabilized = frame_mono_blurred[y_low+dy:y_high+dy, x_low+dx:x_high+dx]
                        frame_stabilized = resize(frame_stabilized, None,
                                                  fx=float(self.scale_factor),
                                                  fy=float(self.scale_factor))
                        font = FONT_HERSHEY_SIMPLEX
                        fontScale = 0.5
                        fontColor = (0, 255, 0)
                        lineType = 1
                        putText(frame_stabilized, 'stabilized: ' + str(dy) + ', ' + str(dx),
                                (5, 25), font, fontScale, fontColor, lineType)

                        frame_dewarped = frame_mono_blurred[y_low+total_shift_y_int:y_high+total_shift_y_int,
                                         x_low+total_shift_x_int:x_high+total_shift_x_int]
                        frame_dewarped = resize(frame_dewarped, None,
                                                  fx=float(self.scale_factor),
                                                  fy=float(self.scale_factor))
                        putText(frame_dewarped, 'de-warped: ' + str(int(round(shift_y))) + ', ' +
                                str(int(round(shift_x))), (5, 25), font, fontScale, fontColor, lineType)
                        # Compose the three patches into a single image and send it to the
                        # visualization window.
                        composed_image = Miscellaneous.compose_image([frame_stabilized,
                                            reference_patch, frame_dewarped],
                                            border=self.border)
                        self.update_image_window_signal.emit(composed_image)
                    except Exception as e:
                        print(str(e))

                    # Insert a delay to keep the current frame long enough in the visualization
                    # window.
                    sleep(self.image_delay)

                # Add the shifted alignment point patch to the AP's stacking buffer.
                self.my_timer.start('Stacking: remapping and adding')
                self.remap_rigid(self.frame_drizzled, alignment_point['stacking_buffer'],
                                 total_shift_y_drizzled, total_shift_x_drizzled,
                                 alignment_point['patch_y_low_drizzled'],
                                 alignment_point['patch_y_high_drizzled'],
                                 alignment_point['patch_x_low_drizzled'],
                                 alignment_point['patch_x_high_drizzled'])
                self.my_timer.stop('Stacking: remapping and adding')

            # If there are holes between AP patches, add this frame's contribution (if any) to the
            # averaged background image.
            if self.number_stacking_holes > 0 and \
                    frame_index in self.rank_frames.quality_sorted_indices[
                        :self.alignment_points.stack_size]:
                self.my_timer.start('Stacking: computing background')

                # Treat the case that the background is computed for specific patches only.
                if self.background_patches:
                    for patch in self.background_patches:
                        self.averaged_background[patch['patch_y_low']:patch['patch_y_high'],
                                  patch['patch_x_low']:patch['patch_x_high']] += \
                            frame[patch['patch_y_low'] + dy : patch['patch_y_high'] + dy,
                                  patch['patch_x_low'] + dx : patch['patch_x_high'] + dx]

                # The complete background image is computed.
                else:
                    self.averaged_background += frame[dy:self.dim_y + dy, dx:self.dim_x + dx]
                self.my_timer.stop('Stacking: computing background')

        if self.progress_signal is not None:
            self.progress_signal.emit("Stack frames", 100)

        # Compute counters for shift distribution analysis.
        shift_counter = sum(self.shift_distribution)
        self.shift_entries_total = shift_counter + self.shift_failure_counter
        if self.shift_entries_total:
            self.shift_failure_percent = round(
                100. * self.shift_failure_counter / self.shift_entries_total, 3)
        else:
            # If the value is <0, the percentage is not printed.
            self.shift_failure_percent = -1.

        # In debug mode: Close de-warp visualization window.
        if self.debug:
            self.terminate_image_window_signal.emit()

        # If a background image is being computed, extend the background buffer into drizzled
        # coordinates and normalize the buffer values.
        if self.number_stacking_holes > 0:
            self.my_timer.start('Stacking: computing background')
            # If drizzling is active, extend the background image into drizzled coordinates.
            if self.drizzle:
                # Be careful: OpenCV resize expects the "width" dimension first!
                self.averaged_background = resize(self.averaged_background,
                                                  (self.dim_x_drizzled, self.dim_y_drizzled),
                                                  interpolation=INTER_CUBIC)
            # Divide the buffer by the number of contributions.
            self.averaged_background /= self.alignment_points.stack_size
            self.my_timer.stop('Stacking: computing background')

    def remap_rigid(self, frame, buffer, shift_y, shift_x, y_low, y_high, x_low, x_high):
        """
        The alignment point patch is taken from the given frame with a constant shift in x and y
        directions. The shifted patch is then added to the given alignment point buffer.

        :param frame: frame to be stacked
        :param buffer: Stacking buffer of the corresponding alignment point
        :param shift_y: Constant shift in y direction between frame stack and current frame
        :param shift_x: Constant shift in x direction between frame stack and current frame
        :param y_low: Lower y index of the quality window on which this method operates
        :param y_high: Upper y index of the quality window on which this method operates
        :param x_low: Lower x index of the quality window on which this method operates
        :param x_high: Upper x index of the quality window on which this method operates
        :return: -
        """

        # Compute index bounds for "source" patch in current frame, and for summation buffer
        # ("target"). Because of local warp effects, the indexing may reach beyond frame borders.
        # In this case reduce the copy area.
        frame_size_y = frame.shape[0]
        y_low_source = y_low + shift_y
        y_high_source = y_high + shift_y
        y_low_target = 0
        # If the shift reaches beyond the frame, reduce the copy area.
        if y_low_source < 0:
            y_low_target = -y_low_source
            y_low_source = 0
            self.border_y_low =  max(self.border_y_low, y_low_target)
        if y_high_source > frame_size_y:
            self.border_y_high = max(self.border_y_high, y_high_source - frame_size_y)
            y_high_source = frame_size_y
        y_high_target = y_low_target + y_high_source - y_low_source

        frame_size_x = frame.shape[1]
        x_low_source = x_low + shift_x
        x_high_source = x_high + shift_x
        x_low_target = 0
        # If the shift reaches beyond the frame, reduce the copy area.
        if x_low_source < 0:
            x_low_target = -x_low_source
            x_low_source = 0
            self.border_x_low = max(self.border_x_low, x_low_target)
        if x_high_source > frame_size_x:
            self.border_x_high = max(self.border_x_high, x_high_source - frame_size_x)
            x_high_source = frame_size_x
        x_high_target = x_low_target + x_high_source - x_low_source

        # If frames are in color, stack all three color channels using the same mapping. Please note
        # that in this case the buffers have a third dimension (color), while monochrome buffers
        # are two-dimensional. Add the frame contribution to the stacking buffer.
        buffer[y_low_target:y_high_target, x_low_target:x_high_target] += \
            frame[y_low_source:y_high_source, x_low_source:x_high_source]

    def merge_alignment_point_buffers(self):
        """
        Merge the summation buffers for all alignment points into the global stacking buffer. For
        every pixel location divide the global buffer by the number of contributing image patches.
        This results in a uniform brightness level across the whole image, even if alignment point
        patches overlap.

        :return: The final stacked image
        """

        self.my_timer.start('Stacking: merging AP buffers')

        # Add the contributions of all alignment points into a single buffer.
        for alignment_point in self.alignment_points.alignment_points:
            patch_y_low_drizzled = alignment_point['patch_y_low_drizzled']
            patch_y_high_drizzled = alignment_point['patch_y_high_drizzled']
            patch_x_low_drizzled = alignment_point['patch_x_low_drizzled']
            patch_x_high_drizzled = alignment_point['patch_x_high_drizzled']

            # Add the stacking buffer of the alignment point to the appropriate location of the
            # global stacking buffer.
            if self.frames.color:
                self.stacked_image_buffer[patch_y_low_drizzled:patch_y_high_drizzled,
                patch_x_low_drizzled: patch_x_high_drizzled, :] += \
                    alignment_point['stacking_buffer'] * alignment_point['weights_yx'][:, :, newaxis]
            else:
                self.stacked_image_buffer[patch_y_low_drizzled:patch_y_high_drizzled,
                patch_x_low_drizzled: patch_x_high_drizzled] += alignment_point['stacking_buffer'] * \
                                              alignment_point['weights_yx']

        # Divide the global stacking buffer pixel-wise by the number of image contributions. Please
        # note that there is no division by zero because the array "sum_single_frame_weights" was
        # initialized to 1.E-30.
        if self.frames.color:
            self.stacked_image_buffer /= self.sum_single_frame_weights[:, :, newaxis]
        else:
            self.stacked_image_buffer /= self.sum_single_frame_weights

        self.my_timer.stop('Stacking: merging AP buffers')

        # If the alignment points do not cover the full frame, blend the AP contributions with
        # a background computed as the average of globally shifted best frames. The background
        # should only shine through outside AP patches.
        if self.number_stacking_holes > 0:
            self.my_timer.create_no_check('Stacking: blending APs with background')

            # The background image has been computed where self.sum_single_frame_weights is below the
            # threshold. Compute for every pixel the weight (between 0. and 1.) with which the
            # stacked patches are to be blended with the background image. Please note that the
            # weights have to be divided by the stack size first, to normalize them to 1. at patch
            # centers.
            foreground_weight = self.sum_single_frame_weights / \
                                     (self.configuration.stack_frames_background_blend_threshold *
                                      self.alignment_points.stack_size)
            clip(foreground_weight, 0., 1., out=foreground_weight)

            # Blend the AP buffer with the background.
            if self.frames.color:
                self.stacked_image_buffer = (self.stacked_image_buffer-self.averaged_background) * \
                                            foreground_weight[:, :, newaxis] + \
                                            self.averaged_background
            else:
                self.stacked_image_buffer = (self.stacked_image_buffer-self.averaged_background) * \
                                            foreground_weight + self.averaged_background

            self.my_timer.stop('Stacking: blending APs with background')

        # Trim the borders of the stacked buffer such that no artifacts from incomplete stacks
        # (caused by warp shifts) remain.
        if self.border_y_low or self.border_y_high or self.border_x_low or self.border_x_high:
            self.stacked_image_buffer = self.stacked_image_buffer[
                                        self.border_y_low:self.dim_y_drizzled - self.border_y_high,
                                        self.border_x_low:self.dim_x_drizzled - self.border_x_high]

        # Scale the image buffer such that entries are in the interval [0., 1.]. Then convert the
        # float image buffer to 16bit int (or 48bit in color mode).
        if self.frames.depth == 8:
            self.stacked_image = img_as_uint(
                clip(self.stacked_image_buffer / 255, 0., 1., out=self.stacked_image_buffer))
        else:
            self.stacked_image = img_as_uint(
                clip(self.stacked_image_buffer / 65535, 0., 1., out=self.stacked_image_buffer))

        return self.stacked_image

    def half_stacked_image_buffer_resolution(self):
        """
        If drizzling is active with the option 1.5x, the computations are performed with the factor
        3x. As a final step, the resolution of the stacked image buffer is halved.

        :return: -
        """

        self.stacked_image = resize(self.stacked_image, None,
                                           fx=float(0.5), fy=float(0.5))

    @staticmethod
    def one_dim_weight(patch_low, patch_high, box_center, extend_low=False, extend_high=False):
        """
        Compute one-dimensional weighting ramps between box center and patch borders. This function
        is called for y and x dimensions separately.

        :param patch_low: Lower index of AP patch in the given coordinate direction
        :param patch_high: Upper index of AP patch in the given coordinate direction
        :param box_center: AP coordinate index in the given direction (center of box)
        :param extend_low: If true, set all weights from patch_low to box_center to 1.
                           (and thus replace the ramp in that index range)
        :param extend_high: If true, set all weights from box_center to patch_high-1 to 1.
                            (and thus replace the ramp in that index range)
        :return: Vector with weights, starting with 1./(box_center - patch_low +1) at patch_low,
                 ramping up to 1. at box_center, and than ramping down to
                 1./(patch_high - box_center) at patch_high-1.
        """

        # Compute offsets relative to patch_low.
        patch_high_offset = patch_high - patch_low
        center_offset = box_center - patch_low

        # Allocate weights array, length given by patch size.
        weights = empty((patch_high_offset,), dtype=float32)

        # If extend_low: Replace lower ramp with constant value 1.
        if extend_low:
            weights[0:center_offset] = 1.
        # Ramp up from a small value to 1. at the center coordinate.
        else:
            weights[0:center_offset] = arange(1, center_offset + 1, 1) / float32(
                center_offset + 1)

        # Now set the weights for indices starting with the center coordinate (weight 1.) and
        # ending at the upper patch boundary with a small value. Again, if "extend_high" is set
        # to True, the ramp is replaced with a constant value 1.
        if extend_high:
            weights[center_offset:patch_high_offset] = 1.
        else:
            weights[center_offset:patch_high_offset] = arange(patch_high - box_center, 0,
                                                                 -1) / float32(
                patch_high - box_center)

        return weights

    def print_shift_table(self):
        """
        Print a table giving for each shift (in pixels) the number of occurrences. The table ends at
        the last non-zero entry.

        :return: String with three lines to be printed to the protocol file(s)
        """

        # Find the last non-zero entry in the array.
        if max(self.shift_distribution) > 0:
            max_index = [index for index, item in enumerate(self.shift_distribution) if item != 0][-1] \
                        + 1

            # Initialize the three table lines.
            s =    "           Shift (pixels):"
            line = "           ---------------"
            t =    "           Percent:       "

            # Extend the three table lines up to the max index.
            for index in range(max_index):
                s += "|{:7d} ".format(index)
                line += "---------"
                t += "|{:7.3f} ".format(100.*self.shift_distribution[index]/self.shift_entries_total)

            # Finish the three table lines.
            s += "|"
            line += "-"
            t += "|"

            # Return the lines to be printed to the protocol.
            return s + "\n" + line + "\n" + t + "\n\n" + \
                   "           Failed shift measurements: {:7.3f} %".format(
                       self.shift_failure_percent)
        else:
            return ""


if __name__ == "__main__":
    # Initalize the timer object used to measure execution times of program sections.
    my_timer = timer()

    # Images can either be extracted from a video file or a batch of single photographs. Select
    # the example for the test run.
    type = 'video'
    if type == 'image':
        names = glob('Images/2012*.tif')
        # names = glob.glob('Images/Moon_Tile-031*ap85_8b.tif')
        # names = glob.glob('Images/Example-3*.jpg')
    else:
        names = 'Videos/another_short_video.avi'
    print(names)

    my_timer.create('Execution over all')

    # Get configuration parameters.
    configuration = Configuration()
    configuration.initialize_configuration()

    my_timer.create('Read all frames')
    try:
        frames = Frames(configuration, names, type=type, bayer_option_selected="Grayscale")
        print("Number of images read: " + str(frames.number))
        print("Image shape: " + str(frames.shape))
    except Error as e:
        print("Error: " + e.message)
        exit()
    my_timer.stop('Read all frames')

    # Rank the frames by their overall local contrast.
    my_timer.create('Ranking images')
    rank_frames = RankFrames(frames, configuration)
    rank_frames.frame_score()
    my_timer.stop('Ranking images')

    print("Index of maximum: " + str(rank_frames.frame_ranks_max_index))
    print("Frame scores: " + str(rank_frames.frame_ranks))
    print("Frame scores (sorted): " + str(
        [rank_frames.frame_ranks[i] for i in rank_frames.quality_sorted_indices]))
    print("Sorted index list: " + str(rank_frames.quality_sorted_indices))

    # Initialize the frame alignment object.
    my_timer.create('Select optimal alignment patch')
    align_frames = AlignFrames(frames, rank_frames, configuration)

    if configuration.align_frames_mode == "Surface":
        # Select the local rectangular patch in the image where the L gradient is highest in both x
        # and y direction. The scale factor specifies how much smaller the patch is compared to the
        # whole image frame.
        (y_low_opt, y_high_opt, x_low_opt, x_high_opt) = align_frames.compute_alignment_rect(
            configuration.align_frames_rectangle_scale_factor)
        my_timer.stop('Select optimal alignment patch')
        print("optimal alignment rectangle, x_low: " + str(x_low_opt) + ", x_high: " + str(
            x_high_opt) + ", y_low: " + str(y_low_opt) + ", y_high: " + str(y_high_opt))
        reference_frame_with_alignment_points = frames.frames_mono(
            rank_frames.frame_ranks_max_index).copy()
        reference_frame_with_alignment_points[y_low_opt,
        x_low_opt:x_high_opt] = reference_frame_with_alignment_points[y_high_opt - 1,
                                x_low_opt:x_high_opt] = 255
        reference_frame_with_alignment_points[y_low_opt:y_high_opt,
        x_low_opt] = reference_frame_with_alignment_points[y_low_opt:y_high_opt, x_high_opt - 1] = 255
        # plt.imshow(reference_frame_with_alignment_points, cmap='Greys_r')
        # plt.show()

    # Align all frames globally relative to the frame with the highest score.
    my_timer.create('Global frame alignment')
    try:
        align_frames.align_frames()
    except NotSupportedError as e:
        print("Error: " + e.message)
        exit()
    except InternalError as e:
        print("Warning: " + e.message)
    my_timer.stop('Global frame alignment')

    # print("Frame shifts: " + str(align_frames.frame_shifts))
    print("Intersection: " + str(align_frames.intersection_shape))

    # Compute the reference frame by averaging the best frames.
    my_timer.create('Compute reference frame')
    average = align_frames.average_frame()
    my_timer.stop('Compute reference frame')
    print("Reference frame computed from the best " + str(
        align_frames.average_frame_number) + " frames.")
    # plt.imshow(align_frames.mean_frame, cmap='Greys_r')
    # plt.show()

    # align_frames.set_roi(100, 700, 200, 700)

    # Initialize the AlignmentPoints object. This includes the computation of the average frame
    # against which the alignment point shifts are measured.
    my_timer.create('Initialize alignment point object')
    alignment_points = AlignmentPoints(configuration, frames, rank_frames, align_frames)
    my_timer.stop('Initialize alignment point object')

    # Create alignment points, and show alignment point boxes and patches.
    my_timer.create('Create alignment points')
    alignment_points.create_ap_grid()
    my_timer.stop('Create alignment points')
    print("Number of alignment points created: " + str(len(alignment_points.alignment_points)) +
          ", number of dropped aps (dim): " + str(alignment_points.alignment_points_dropped_dim) +
          ", number of dropped aps (structure): " + str(
          alignment_points.alignment_points_dropped_structure))

    # color_image = alignment_points.show_alignment_points(average)
    # plt.imshow(color_image)
    # plt.show()

    # For each alignment point rank frames by their quality.
    my_timer.create('Rank frames at alignment points')
    alignment_points.compute_frame_qualities()
    my_timer.stop('Rank frames at alignment points')

    # Allocate StackFrames object.
    stack_frames = StackFrames(configuration, frames, rank_frames, align_frames, alignment_points, my_timer)

    # Stack all frames.
    stack_frames.stack_frames()

    # Merge the stacked alignment point buffers into a single image.
    stacked_image = stack_frames.merge_alignment_point_buffers()

    # If the drizzle factor is 1.5, reduce the pixel resolution of the stacked image buffer
    # to half the size used in stacking.
    if configuration.drizzle_factor_is_1_5:
        stack_frames.half_stacked_image_buffer_resolution()

    # Save the stacked image as 16bit int (color or mono).
    Frames.save_image('Images/example_stacked.tiff', stacked_image, color=frames.color,
                      header=configuration.global_parameters_version)

    # Convert to 8bit and show in Window.
    plt.imshow(img_as_ubyte(stacked_image))
    plt.show()

    # Print out timer results.
    my_timer.stop('Execution over all')
    my_timer.print()
