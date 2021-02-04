#!/usr/bin/python3
import fnmatch
import struct
import sys

import os

from datetime import datetime

from PyQt5 import QtWidgets, uic, QtCore, QtGui
from PyQt5.QtCore import QDir, QTimer
from PyQt5.QtGui import QStandardItemModel, QStandardItem, QKeySequence, QClipboard
from PyQt5.QtWidgets import QFileSystemModel, QTreeView, QShortcut, QApplication

import matplotlib
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg

matplotlib.use('QT5Agg')
import matplotlib.pylab as plt

from spectrum import Spectrum


class MplCanvas(FigureCanvasQTAgg):

	def __init__(self, parent=None, width=5, height=4, dpi=100):
		self.fig = plt.figure(figsize=(width, height), dpi=dpi)
		self.axes = self.fig.add_subplot(111)
		super(MplCanvas, self).__init__(self.fig)


class SearchProxyModel(QtCore.QSortFilterProxyModel):

	def setFilterRegExp(self, pattern):
		if isinstance(pattern, str):
			pattern = QtCore.QRegExp(pattern)
		super(SearchProxyModel, self).setFilterRegExp(pattern)

	def _accept_index(self, idx):
		if idx.isValid():
			text = idx.data(QtCore.Qt.DisplayRole)
			if self.filterRegExp().indexIn(text) >= 0:
				return True
			for row in range(idx.model().rowCount(idx)):
				if self._accept_index(idx.model().index(row, 0, idx)):
					return True
		return False

	def filterAcceptsRow(self, sourceRow, sourceParent):
		idx = self.sourceModel().index(sourceRow, 0, sourceParent)
		return self._accept_index(idx)

	# ignore CUR and SEQ when sorting
	def lessThan(self, left, right):
		leftData = self.sourceModel().data(left).replace('CUR', '').replace('SEQ', '')
		rightData = self.sourceModel().data(right).replace('CUR', '').replace('SEQ', '')
		return leftData < rightData


class MainWindow(QtWidgets.QMainWindow):

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)

		ui_path = os.path.dirname(os.path.abspath(__file__))
		uic.loadUi(os.path.join(ui_path, "mainwindow.ui"), self)

		if len(sys.argv) > 1 and sys.argv[1] == '.':
			path = os.getcwd() + '/'
		elif len(sys.argv) == 1:
			path = '/'
		else:
			path = sys.argv[1]
		self.initUi(path)

	def initUi(self, path):
		self._dirpath = os.path.dirname(path)

		self.model = QFileSystemModel()
		self.model.setRootPath(self._dirpath)
		self.model.setFilter(QtCore.QDir.AllDirs | QtCore.QDir.NoDotAndDotDot)

		self.proxy_model = SearchProxyModel()
		self.proxy_model.setFilterRole(QtWidgets.QFileSystemModel.FileNameRole)
		self.proxy_model.setSourceModel(self.model)
		self.proxy_model.setDynamicSortFilter(True)
		self.filesystemTree.setModel(self.proxy_model)

		self.adjust_root_index()

		# hide unnecesasry columns, we care only about name
		columns = self.filesystemTree.header()
		for c in range(1, columns.count()):
			columns.setSectionHidden(c, True)

		self.filesystemTree.setAnimated(False)
		self.filesystemTree.setIndentation(20)
		self.filesystemTree.setSortingEnabled(True)

		## clipboard for copying sequence name when double clicked
		self.clipboard = QApplication.clipboard()

		self.canvas = MplCanvas(self, width=5, height=4, dpi=100)
		lay = self.specHbox
		lay.addWidget(self.canvas)
		lay.setStretch(0, 1)

		## exit shortcut
		self.quitSc = QShortcut(QKeySequence('Ctrl+Q'), self)
		self.quitSc.activated.connect(QApplication.instance().quit)

		## 1 s timer for filesystem tree update (needed for network filesystems)
		self.timer = QTimer()
		self.timer.timeout.connect(self.refresh_fstree)
		self.timer.setSingleShot(False)
		self.timer.start(1000)

		## signals
		self.filesystemFilter.textChanged.connect(self.on_textChanged)


	def refresh_fstree(self):
		self.model.setRootPath('')
		self.model.setRootPath(self._dirpath)


	def on_textChanged(self):
		regExp = QtCore.QRegExp(self.filesystemFilter.text(), QtCore.Qt.CaseInsensitive, QtCore.QRegExp.WildcardUnix)
		self.proxy_model.text = self.filesystemFilter.text().lower()
		self.proxy_model.setFilterRegExp(regExp)
		self.adjust_root_index()
	

	def adjust_root_index(self):
		root_index = self.model.index(self._dirpath)
		proxy_index = self.proxy_model.mapFromSource(root_index)
		self.filesystemTree.setRootIndex(proxy_index)


	@QtCore.pyqtSlot(QtCore.QModelIndex)
	def on_filesystemTree_doubleClicked(self, index):
		proxyIndexItem = self.proxy_model.index(index.row(), 0, index.parent())
		indexItem = self.proxy_model.mapToSource(proxyIndexItem)

		self.selected_seq_filename = self.model.fileName(indexItem)

		## use selection clipboard if available (X11, paste with middle mouse button)
		if self.clipboard.supportsSelection() == True:
			self.clipboard.clear(mode=QClipboard.Selection)
			self.clipboard.setText(self.selected_seq_filename, mode=QClipboard.Selection)
		else:
			self.clipboard.clear(mode=QClipboard.Clipboard)
			self.clipboard.setText(self.selected_seq_filename, mode=QClipboard.Clipboard)

		self.statusBar.showMessage('Copied \''+self.selected_seq_filename+'\' to clipboard')


	@QtCore.pyqtSlot(QtCore.QModelIndex)
	def on_filesystemTree_clicked(self, index):
		proxyIndexItem = self.proxy_model.index(index.row(), 0, index.parent())
		indexItem = self.proxy_model.mapToSource(proxyIndexItem)

		filePath = self.model.filePath(indexItem)
		self.selected_seq_filename = self.model.fileName(indexItem)

		# get list of sequences in sequence folder
		self.spectra_path = os.path.join(filePath, "RADIOMETER")
		lst = os.listdir(self.spectra_path)
		lst.sort()	
		self.sequenceList = fnmatch.filter(lst, "*.spe")
		self.seriesListModel = QStandardItemModel(self.seriesList)
		for i in self.sequenceList:
			itm = QStandardItem(i)
			self.seriesListModel.appendRow(itm)
		self.seriesList.setModel(self.seriesListModel)
		self.seriesList.clicked.connect(self.on_sequenceList_clicked)


	@QtCore.pyqtSlot(QtCore.QModelIndex)
	def on_sequenceList_clicked(self, index):
		self.selected_seq_name = self.sequenceList[index.row()]
		with open(os.path.join(self.spectra_path, self.selected_seq_name), mode="rb") as file:
			raw = file.read()
		filesize = len(raw)
		byte_pointer = 0
		chunk_counter = 1
		self.spectra_list = []
		while filesize - byte_pointer:
			chunk_size = struct.unpack('<H', raw[byte_pointer:byte_pointer+2])[0]
			chunk_body = raw[byte_pointer:byte_pointer+chunk_size]
			spectrum = Spectrum.parse_raw(chunk_body)
			byte_pointer += chunk_size
			chunk_counter += 1
			self.spectra_list.append(spectrum)
		self.spectraListModel = QStandardItemModel(self.spectraListView)
		for i in range(len(self.spectra_list)):
			itm = QStandardItem(str(i + 1) + "-" + str(self.spectra_list[i].header.timestamp) + "-" + self.spectra_list[i].header.spectrum_type.radiometer.name + "-" + self.spectra_list[i].header.spectrum_type.optics.name)
			self.spectraListModel.appendRow(itm)
		self.spectraListView.setModel(self.spectraListModel)
		sel_model = self.spectraListView.selectionModel()
		sel_model.currentChanged.connect(self.on_spectrumList_clicked)


	@QtCore.pyqtSlot(QtCore.QModelIndex)
	def on_spectrumList_clicked(self, index):
		self.plotted_spec = self.spectra_list[index.row()]
		self.ts_val.setText(datetime.utcfromtimestamp(self.plotted_spec.header.timestamp / 1000).strftime('%Y-%m-%d %H:%M:%S'))
		self.rad_val.setText(self.plotted_spec.header.spectrum_type.radiometer.name)
		self.entrance_val.setText(self.plotted_spec.header.spectrum_type.optics.name)
		self.it_val.setText(str(self.plotted_spec.header.exposure_time))
		self.pix_count_val.setText(str(self.plotted_spec.header.pixel_count))
		self.temp_val.setText("{:.2f}".format(self.plotted_spec.header.temperature))
		self.x_val.setText("{:.2f} ±{:.2f}".format(self.plotted_spec.header.accel_stats.mean_x * 19.6 / 32768.0, self.plotted_spec.header.accel_stats.std_x * 19.6 / 32768.0))
		self.y_val.setText("{:.2f} ±{:.2f}".format(self.plotted_spec.header.accel_stats.mean_y * 19.6 / 32768.0, self.plotted_spec.header.accel_stats.std_y * 19.6 / 32768.0))
		self.z_val.setText("{:.2f} ±{:.2f}".format(self.plotted_spec.header.accel_stats.mean_z * 19.6 / 32768.0, self.plotted_spec.header.accel_stats.std_z * 19.6 / 32768.0))

		self.canvas.axes.cla()  # clear canvas
		self.canvas.axes.plot(range(self.plotted_spec.header.pixel_count), self.plotted_spec.body, 'r-')
		self.canvas.axes.set_xlabel("Pixel number")
		self.canvas.axes.set_ylabel("Raw DN")

		## add title to graph
		if self.graphTitle.isChecked():
			self.canvas.fig.suptitle(datetime.utcfromtimestamp(self.plotted_spec.header.timestamp / 1000).strftime('%Y-%m-%d %H:%M:%S'))
			self.canvas.axes.set_title(self.plotted_spec.header.spectrum_type.radiometer.name + " " + self.plotted_spec.header.spectrum_type.optics.name + ", " + str(self.plotted_spec.header.exposure_time) + "ms, {:.2f} °C".format(self.plotted_spec.header.temperature))
		else:
			self.canvas.fig.suptitle('')

		## autoscale y axis
		if self.autoScaleY.isChecked() == 0:
			self.canvas.axes.set_ylim([0, 65535])

		self.canvas.draw()
		self.saveButton.setEnabled(True)
		self.plotted_spectrum_number = index.row() + 1


	@QtCore.pyqtSlot(int)
	def on_autoScaleY_stateChanged(self, state):
		if state == 0:
			self.canvas.axes.set_ylim([0, 65535])
		else:
			self.canvas.axes.autoscale(axis='y')

		self.canvas.draw()


	@QtCore.pyqtSlot(int)
	def on_graphTitle_stateChanged(self, state):
		if self.graphTitle.isChecked():
			self.canvas.fig.suptitle(datetime.utcfromtimestamp(self.plotted_spec.header.timestamp / 1000).strftime('%Y-%m-%d %H:%M:%S'))
			self.canvas.axes.set_title(self.plotted_spec.header.spectrum_type.radiometer.name + " " + self.plotted_spec.header.spectrum_type.optics.name + ", " + str(self.plotted_spec.header.exposure_time) + "ms, {:.2f} °C".format(self.plotted_spec.header.temperature))
		else:
			self.canvas.fig.suptitle('')
			self.canvas.axes.set_title('')

		self.canvas.draw()
		

	@QtCore.pyqtSlot(bool)
	def on_saveButton_clicked(self, checked):
		name, flt = QtWidgets.QFileDialog.getSaveFileName(self, 'Save File', self.selected_seq_filename + "_" + self.selected_seq_name.split("_")[1] + "_" + str(self.plotted_spectrum_number) + ".png")
		if name != '':
			self.statusBar.showMessage('Saved graph to '+name)
			self.canvas.fig.savefig(name)


if __name__ == '__main__':
	app = QtWidgets.QApplication(sys.argv)
	window = MainWindow()
	window.show()
	app.exec_()
