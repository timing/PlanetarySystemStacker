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
from statistics import mean
from time import time

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from cv2 import meanStdDev
from numpy import array, full

from configuration import Configuration
from exceptions import ArgumentError, NotSupportedError, Error
from frames import Frames
from miscellaneous import Miscellaneous


class RankFrames(object):
    """
        Rank frames according to their overall sharpness. Experiments with different algorithms
        have been made. The classical "Sobel" algorithm is good but slow. An alternative is
        implemented in method "local_contrast" in module "miscellaneous".

    """

    def __init__(self, frames, configuration, progress_signal=None):
        """
        Initialize the object and instance variables.

        :param frames: Frames object with all video frames
        :param configuration: Configuration object with parameters
        :param progress_signal: Either None (no progress signalling), or a signal with the signature
                                (str, int) with the current activity (str) and the progress in
                                percent (int).
        """


        self.shape = frames.shape
        self.configuration = configuration
        self.frames = frames

        self.number_original = frames.number
        self.frame_ranks_original = []
        self.quality_sorted_indices_original = None
        self.rank_indices_original = None
        self.frame_ranks_max_index_original = None
        self.frame_ranks_max_value_original = None

        self.number = None
        self.frame_ranks = None
        self.quality_sorted_indices = None
        self.rank_indices = None
        self.frame_ranks_max_index = None
        self.frame_ranks_max_value = None

        self.progress_signal = progress_signal
        self.signal_step_size = max(int(self.number_original / 10), 1)

    def frame_score(self):
        """
        Compute the frame quality values and normalize them such that the best value is 1.

        :return: -
        """

        if self.configuration.rank_frames_method == "xy gradient":
            method = Miscellaneous.local_contrast
        elif self.configuration.rank_frames_method == "Laplace":
            method = Miscellaneous.local_contrast_laplace
        elif self.configuration.rank_frames_method == "Sobel":
            method = Miscellaneous.local_contrast_sobel
        else:
            raise NotSupportedError("Ranking method " + self.configuration.rank_frames_method +
                                    " not supported")

        # Reset frames index translation, if active.
        if self.frames.index_translation_active:
            self.frames.reset_index_translation()

        # For all frames compute the quality with the selected method.
        if method != Miscellaneous.local_contrast_laplace:
            for frame_index in range(self.number_original):
                frame = self.frames.frames_mono_blurred(frame_index)
                if self.progress_signal is not None and frame_index % self.signal_step_size == 1:
                    self.progress_signal.emit("Rank all frames",
                                              int(round(10*frame_index / self.number_original) * 10))
                if self.configuration.frames_normalization:
                    self.frame_ranks_original.append(
                        method(frame, self.configuration.rank_frames_pixel_stride) /
                        self.frames.average_brightness(frame_index))
                else:
                    self.frame_ranks_original.append(
                        method(frame, self.configuration.rank_frames_pixel_stride))
        else:
            for frame_index in range(self.number_original):
                frame = self.frames.frames_mono_blurred_laplacian(frame_index)
                # self.frame_ranks.append(mean((frame - frame.mean())**2))
                if self.progress_signal is not None and frame_index % self.signal_step_size == 1:
                    self.progress_signal.emit("Rank all frames",
                                              int(round(10*frame_index / self.number_original) * 10))
                if self.configuration.frames_normalization:
                    self.frame_ranks_original.append(meanStdDev(frame)[1][0][0] /
                        self.frames.average_brightness(frame_index))
                else:
                    self.frame_ranks_original.append(meanStdDev(frame)[1][0][0])

        # Sort the frame indices in descending order of quality.
        self.quality_sorted_indices_original = sorted(range(self.number_original),
                                             key=self.frame_ranks_original.__getitem__, reverse=True)

        # Compute the inverse index list: For each frame the rank_index is the corresponding index
        # in the sorted frame_ranks list.
        self.rank_indices_original = [self.quality_sorted_indices_original.index(index) for index in
                             range(self.number_original)]

        if self.progress_signal is not None:
            self.progress_signal.emit("Rank all frames", 100)

        # Set the index of the best frame, and normalize all quality values.
        self.frame_ranks_max_index_original = self.quality_sorted_indices_original[0]
        self.frame_ranks_max_value_original = self.frame_ranks_original[self.frame_ranks_max_index_original]
        self.frame_ranks_original /= self.frame_ranks_max_value_original

        # Keep the original ranking data and prepare for index translation. The translation can be
        # reset later, and the original ranking be re-established.
        self.number = self.number_original
        self.frame_ranks = self.frame_ranks_original
        self.quality_sorted_indices = self.quality_sorted_indices_original
        self.rank_indices = self.rank_indices_original
        self.frame_ranks_max_index = self.frame_ranks_max_index_original
        self.frame_ranks_max_value = self.frame_ranks_max_value_original

    def set_index_translation(self, index_translation):
        """
        After frames have been marked to be excluded from the further workflow, update the ranking
        tables, based on the index translation list from the frames module.

        :param index_translation: List with indices. For each index in the reduced list of frames
                                  it gives the corresponding index in the original frame list.
        :return: -
        """

        # Set the number of ranks to the number of included frames.
        self.number = len(index_translation)

        self.frame_ranks = [self.frame_ranks_original[index] for index in index_translation]

        # Sort the frame indices in descending order of quality.
        self.quality_sorted_indices = sorted(range(self.number),
                                             key=self.frame_ranks.__getitem__, reverse=True)

        # Compute the inverse index list: For each frame the rank_index is the corresponding index
        # in the sorted frame_ranks list.
        self.rank_indices = [self.quality_sorted_indices.index(index) for index in
                             range(self.number)]

        if self.progress_signal is not None:
            self.progress_signal.emit("Rank all frames", 100)

        # Set the index of the best frame, and normalize all quality values.
        self.frame_ranks_max_index = self.quality_sorted_indices[0]
        self.frame_ranks_max_value = self.frame_ranks[self.frame_ranks_max_index]
        self.frame_ranks /= self.frame_ranks_max_value

    def reset_index_translation(self):
        """
        De-activate index translation and re-establish the original frame ranking data.

        :return: -
        """

        self.number = self.number_original
        self.frame_ranks = self.frame_ranks_original
        self.quality_sorted_indices = self.quality_sorted_indices_original
        self.rank_indices = self.rank_indices_original
        self.frame_ranks_max_index = self.frame_ranks_max_index_original
        self.frame_ranks_max_value = self.frame_ranks_max_value_original

    def find_best_frames(self, number_frames, region_size):
        """
        Find the indices of the best "number_frames" frames under the condition that all indices
        are within an interval of size "region_size".

        :param number_frames: Number of best frames the indices of which are to be found.
        :param region_size: Maximal width of index interval.
        :return: (List of frame indices, quality loss, time line position) with:
                List of frame indices: Indices of frames participating in mean frame computation.
                quality loss: Loss in average frame quality due to range restriction (%).
                time line position: Position of the average frame index relative to the total
                                    duration of the video.
        """

        # Check input arguments for validity.
        if number_frames > region_size:
            raise ArgumentError("Attempt to find " + str(number_frames) + " good frames in "
                                "an index interval of size " + str(region_size))
        elif region_size > self.number:
            raise ArgumentError("Size of best frames region " + str(region_size) + " larger "
                                "than the total number of frames " + str(self.number))

        best_indices = []
        rank_sum_opt = 0.

        # Construct a sliding window on the full index range. For each window position find the
        # best "number_frames" frames. Find the window and the best frame set within with the
        # highest overall score.
        for start_index in range(self.number - region_size + 1):
            end_index = start_index + region_size
            best_indices_in_range = sorted(range(start_index, end_index),
                                           key=self.frame_ranks.__getitem__, reverse=True)[
                                    :number_frames]
            rank_sum = sum([self.frame_ranks[i] for i in best_indices_in_range])
            if rank_sum > rank_sum_opt:
                rank_sum_opt = rank_sum
                best_indices = best_indices_in_range

        # Compare the average frame quality with the optimal choice if no time restrictions were
        # present.
        rank_sum_global = sum(
            [self.frame_ranks[i] for i in self.quality_sorted_indices[:number_frames]])
        quality_loss_percent = round(100. * (rank_sum_global - rank_sum_opt) / rank_sum_global, 1)

        # For the frames included in mean frame computation compute the average position on the
        # video time line.
        cog_mean_frame = round(100 * mean(best_indices) / self.number, 1)

        return best_indices, quality_loss_percent, cog_mean_frame


if __name__ == "__main__":

    # Images can either be extracted from a video file or a batch of single photographs. Select
    # the example for the test run.
    type = 'video'
    if type == 'image':
        # names = glob.glob('Images/2012*.tif')
        # names = glob.glob('Images/Moon_Tile-031*ap85_8b.tif')
        # names = glob.glob('Images/Example-3*.jpg')
        names = glob('Images/Mond_*.jpg')
    else:
        names = 'Videos/another_short_video.avi'
        # names = "E:\SW-Development\Python\PlanetarySystemStacker\Examples\Moon_2018-03-24\Moon_Tile-024_043939.avi"
        # names = 'Videos/Moon_Tile-024_043939.avi'
    print(names)

    # Get configuration parameters.
    configuration = Configuration()
    configuration.initialize_configuration()
    try:
        frames = Frames(configuration, names, type=type)
        print("Number of images read: " + str(frames.number))
        print("Image shape: " + str(frames.shape))
    except Error as e:
        print("Error: " + e.message)
        exit()

    # Rank the frames by their overall local contrast.
    start = time()
    rank_frames = RankFrames(frames, configuration)
    rank_frames.frame_score()
    end = time()
    print('Elapsed time in ranking all frames: {}'.format(end - start))

    # for rank, index in enumerate(rank_frames.quality_sorted_indices):
    #     frame_quality = rank_frames.frame_ranks[index]
    #     print("Rank: " + str(rank) + ", Frame no. " + str(index) + ", quality: " + str(frame_quality))
    # for index, frame_quality in enumerate(rank_frames.frame_ranks):
    #     rank = rank_frames.quality_sorted_indices.index(index)
    #     print("Frame no. " + str(index) + ", Rank: " + str(rank) + ", quality: " +
    #           str(frame_quality))

    print("")
    num_frames = len(rank_frames.frame_ranks)
    frame_percent = 10
    num_frames_stacked = max(1, round(num_frames*frame_percent/100.))
    print("Percent of frames to be stacked: ", str(frame_percent), ", numnber: "
           + str(num_frames_stacked))
    quality_cutoff = rank_frames.frame_ranks[rank_frames.quality_sorted_indices[num_frames_stacked]]
    print("Quality cutoff: ", str(quality_cutoff))

    # Plot the frame qualities in chronological order.
    ax1 = plt.subplot(211)

    x = array(rank_frames.frame_ranks)
    plt.ylabel('Frame number')
    plt.gca().invert_yaxis()
    y = array(range(num_frames))
    x_cutoff = full((num_frames,), quality_cutoff)
    plt.xlabel('Quality')
    line1, = plt.plot(x, y, lw=1)
    line2, = plt.plot(x_cutoff, y, lw=1)
    index = 37
    plt.scatter(x[index], y[index], s=20)
    plt.grid(True)

    # Plot the frame qualities ordered by value.
    ax2 = plt.subplot(212)

    x = array([rank_frames.frame_ranks[i] for i in rank_frames.quality_sorted_indices])
    plt.ylabel('Frame rank')
    plt.gca().invert_yaxis()
    y = array(range(num_frames))
    y_cutoff = full((num_frames,), num_frames_stacked)
    plt.xlabel('Quality')
    line3, = plt.plot(x, y, lw=1)
    line4, = plt.plot(x, y_cutoff, lw=1)
    index = 37
    plt.scatter(x[index], y[index], s=20)
    plt.grid(True)

    plt.show()

    number = 3
    window = 5
    start = time()
    best_indices, quality_loss_percent, cog_mean_frame = rank_frames.find_best_frames(number, window)
    end = time()
    print ("\nIndices of best frames in window of size " + str(window) + " found in " +
           str(end - start) + " seconds: " + str(best_indices) +
           "\nQuality loss as compared to unrestricted selection: " +
           str(quality_loss_percent) + "%\nPosition of mean frame in video time line: " +
           str(cog_mean_frame) + "%")
