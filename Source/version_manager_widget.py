# -*- coding: utf-8 -*-

# Form implementation generated from reading ui file 'version_manager_widget.ui'
#
# Created by: PyQt5 UI code generator 5.11.2
#
# WARNING! All changes made in this file will be lost!

from PyQt5 import QtCore, QtGui, QtWidgets

class Ui_version_manager_widget(object):
    def setupUi(self, version_manager_widget):
        version_manager_widget.setObjectName("version_manager_widget")
        version_manager_widget.resize(401, 124)
        self.verticalLayout = QtWidgets.QVBoxLayout(version_manager_widget)
        self.verticalLayout.setObjectName("verticalLayout")
        self.groupBox = QtWidgets.QGroupBox(version_manager_widget)
        self.groupBox.setObjectName("groupBox")
        self.gridLayout_2 = QtWidgets.QGridLayout(self.groupBox)
        self.gridLayout_2.setObjectName("gridLayout_2")
        self.pushButton_new = QtWidgets.QPushButton(self.groupBox)
        self.pushButton_new.setObjectName("pushButton_new")
        self.gridLayout_2.addWidget(self.pushButton_new, 1, 0, 1, 2)
        self.pushButton_delete = QtWidgets.QPushButton(self.groupBox)
        self.pushButton_delete.setObjectName("pushButton_delete")
        self.gridLayout_2.addWidget(self.pushButton_delete, 1, 2, 1, 2)
        self.pushButton_save = QtWidgets.QPushButton(self.groupBox)
        self.pushButton_save.setObjectName("pushButton_save")
        self.gridLayout_2.addWidget(self.pushButton_save, 1, 4, 1, 2)
        self.pushButton_save_as = QtWidgets.QPushButton(self.groupBox)
        self.pushButton_save_as.setObjectName("pushButton_save_as")
        self.gridLayout_2.addWidget(self.pushButton_save_as, 1, 6, 1, 2)
        self.spinBox_compare = QtWidgets.QSpinBox(self.groupBox)
        self.spinBox_compare.setObjectName("spinBox_compare")
        self.gridLayout_2.addWidget(self.spinBox_compare, 0, 7, 1, 1)
        self.checkBox_blink_compare = QtWidgets.QCheckBox(self.groupBox)
        self.checkBox_blink_compare.setObjectName("checkBox_blink_compare")
        self.gridLayout_2.addWidget(self.checkBox_blink_compare, 0, 4, 1, 3)
        self.spinBox_version = QtWidgets.QSpinBox(self.groupBox)
        self.spinBox_version.setObjectName("spinBox_version")
        self.gridLayout_2.addWidget(self.spinBox_version, 0, 2, 1, 1)
        self.label_version = QtWidgets.QLabel(self.groupBox)
        self.label_version.setObjectName("label_version")
        self.gridLayout_2.addWidget(self.label_version, 0, 0, 1, 2)
        self.verticalLayout.addWidget(self.groupBox)

        self.retranslateUi(version_manager_widget)
        QtCore.QMetaObject.connectSlotsByName(version_manager_widget)

    def retranslateUi(self, version_manager_widget):
        _translate = QtCore.QCoreApplication.translate
        version_manager_widget.setWindowTitle(_translate("version_manager_widget", "Form"))
        self.groupBox.setTitle(_translate("version_manager_widget", "Version manager"))
        self.pushButton_new.setText(_translate("version_manager_widget", "New"))
        self.pushButton_delete.setText(_translate("version_manager_widget", "Delete"))
        self.pushButton_save.setText(_translate("version_manager_widget", "Save"))
        self.pushButton_save_as.setText(_translate("version_manager_widget", "Save as"))
        self.checkBox_blink_compare.setText(_translate("version_manager_widget", "Blink compare with"))
        self.label_version.setText(_translate("version_manager_widget", "Version selected"))

