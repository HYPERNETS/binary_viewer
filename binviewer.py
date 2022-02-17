#!/usr/bin/python3
import fnmatch
import struct
import sys
import numpy

import os

from datetime import datetime

from PyQt5 import QtWidgets, uic, QtCore, QtGui
from PyQt5.QtCore import QDir, QTimer, QEvent
from PyQt5.QtGui import QStandardItemModel, QStandardItem, QKeySequence, QClipboard
from PyQt5.QtWidgets import QFileSystemModel, QTreeView, QShortcut, QApplication

import matplotlib
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg

matplotlib.use('QT5Agg')
import matplotlib.pyplot as plt

from spectrum import Spectrum, Radiometer


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

		ui_path = os.path.dirname(os.path.realpath(__file__))
		uic.loadUi(os.path.join(ui_path, "mainwindow.ui"), self)

		if len(sys.argv) > 1 and sys.argv[1] == '.':
			path = os.path.join(os.getcwd(), '')
		elif len(sys.argv) == 1:
			path = '/'
		else:
			path = os.path.join(sys.argv[1], '')
		self.initUi(path)

		## hypstar_220261_wl_coefs_220105.dat, L coefs
		self.wlcoefs_v = [-3.86230580363333e-12, 1.06280450160733e-09, 3.49165721007352e-05, 0.436208228946683, 165.131629776554]
		self.wlcoefs_s = [-4.30538111771684e-10, -1.4090756944986e-06, -0.00149472890559243, 3.63649691365231, 875.424427118224]


	def px2wl(self, px, coefs):
		p = numpy.poly1d(coefs)
		return p(px)


	def initUi(self, path):
		self._dirpath = os.path.dirname(path)

		## filesystem model
		self.model = QFileSystemModel()
		self.model.setRootPath(self._dirpath)
		self.model.setFilter(QtCore.QDir.AllDirs | QtCore.QDir.NoDotAndDotDot)

		self.proxy_model = SearchProxyModel()
		self.proxy_model.setFilterRole(QtWidgets.QFileSystemModel.FileNameRole)
		self.proxy_model.setSourceModel(self.model)
		self.proxy_model.setDynamicSortFilter(True)
		self.filesystemTree.setModel(self.proxy_model)

		self.filesystemFilter.textChanged.connect(self.on_textChanged) # connect search box signal

		## hide unnecesasry columns, we care only about name
		columns = self.filesystemTree.header()
		for c in range(1, columns.count()):
			columns.setSectionHidden(c, True)

		self.filesystemTree.setAnimated(False)
		self.filesystemTree.setIndentation(20)
		self.filesystemTree.setSortingEnabled(True)

		sel_model = self.filesystemTree.selectionModel()

		## connect current changed signal
		## using current changed instead of clicked allows seleting with arrow keys on keyboard
		sel_model.currentChanged.connect(self.on_filesystemTree_currentChanged)
		
		self.adjust_root_index() # populate filesystem tree

		## install event filter on filesystem tree viewport for catching pressed event before selection change
		## this way the already selected sequence can be refreshed by clicking on it (e.g. when sequence is running)
		self.filesystemTree.viewport().installEventFilter(self)

		## 1 s timer for filesystem tree update (needed for network filesystems)
		self.timer = QTimer()
		self.timer.timeout.connect(self.refresh_fstree)
		self.timer.setSingleShot(False)
		self.timer.start(1000)

		## series list model
		self.seriesListModel = QStandardItemModel(self.seriesList)
		self.seriesList.setModel(self.seriesListModel)
		sel_model = self.seriesList.selectionModel()
		sel_model.currentChanged.connect(self.on_sequenceList_currentChanged) # connect current changed signal

		## spectra list model
		self.spectraListModel = QStandardItemModel(self.spectraListView)
		self.spectraListView.setModel(self.spectraListModel)
		sel_model = self.spectraListView.selectionModel()
		sel_model.currentChanged.connect(self.on_spectrumList_currentChanged) # connect current changed signal

		## clipboard for copying sequence name when double clicked
		self.clipboard = QApplication.clipboard()

		## graph area
		self.canvas = MplCanvas(self, width=5, height=4, dpi=100)
		lay = self.specHbox
		lay.addWidget(self.canvas)
		lay.setStretch(0, 1)

		## exit shortcut
		self.quitSc = QShortcut(QKeySequence('Ctrl+Q'), self)
		self.quitSc.activated.connect(QApplication.instance().quit)


	## filesystem tree viewport event filter
	def eventFilter(self, obj, event):
		if event.type() == QEvent.MouseButtonPress:  # Catch the mouse button press event
			index = self.filesystemTree.indexAt(event.pos())
			proxyIndexItem = self.proxy_model.index(index.row(), 0, index.parent())
			indexItem = self.proxy_model.mapToSource(proxyIndexItem)
			sel_model = self.filesystemTree.selectionModel()

			## refresh if already selected, otherwise the refresh will be handled on selectionChanged event
			if sel_model.isSelected(index) == True:
				self.on_filesystemTree_currentChanged(index)
				return False

		## pass all the other events to parent class
		return super().eventFilter(obj, event)


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
	def on_filesystemTree_currentChanged(self, index):
		proxyIndexItem = self.proxy_model.index(index.row(), 0, index.parent())
		indexItem = self.proxy_model.mapToSource(proxyIndexItem)

		filePath = self.model.filePath(indexItem)
		self.selected_seq_filename = self.model.fileName(indexItem)

		# get list of sequences in sequence folder
		self.spectra_path = os.path.join(filePath, "RADIOMETER")
		lst = os.listdir(self.spectra_path)
		lst.sort()	
		self.sequenceList = fnmatch.filter(lst, "*.spe") + fnmatch.filter(lst, '*.jpg')
		self.sequenceList.sort()
		self.seriesListModel.clear() # clear sequence list
		for i in self.sequenceList:
			itm = QStandardItem(i)
			self.seriesListModel.appendRow(itm)

		## select first sequence in the list for plotting
		sel_model = self.seriesList.selectionModel()
		sel_model.setCurrentIndex(self.seriesListModel.index(0, 0), QtCore.QItemSelectionModel.SelectCurrent)


	@QtCore.pyqtSlot(QtCore.QModelIndex)
	def on_sequenceList_currentChanged(self, index):
		self.selected_seq_name = self.sequenceList[index.row()]

		self.spectraListModel.clear() # clear list of spectra

		## image
		if self.selected_seq_name.find("jpg") != -1:
			self.canvas.fig.suptitle('') # clear title
			self.canvas.axes.cla()  # clear canvas
			self.canvas.axes.axis('off') # hide axes
			self.autoScaleY.setEnabled(False) # disable Y axis autoscale checkbox
			self.wlScale.setEnabled(False) # disable wavelength scale checkbox
			self.graphTitle.setEnabled(False) # disable graph title checkbox
			self.saveButton.setEnabled(False) # disable save button

			if os.stat(os.path.join(self.spectra_path, self.selected_seq_name)).st_size == 0:
				self.canvas.draw() # draw the empty canvas
				return ## zero size

			img = matplotlib.image.imread(os.path.join(self.spectra_path, self.selected_seq_name))
			self.canvas.axes.imshow(img) # show image
			self.canvas.draw()

			## header fields
			self.ts_val.clear()
			self.rad_val.setText("CAMERA")
			self.entrance_val.clear()
			self.it_val.clear()
			self.pix_count_val.setText(f"{img.shape[0]}x{img.shape[1]} ({img.shape[0] * img.shape[1] * 1e-6:.1f} MP)")
			self.temp_val.clear()
			self.x_val.clear()
			self.y_val.clear()
			self.z_val.clear()
			return

		## spectra
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

		## fill in spectra list
		for i in range(len(self.spectra_list)):
			itm = QStandardItem(str(i + 1) + "-" + str(self.spectra_list[i].header.timestamp) + "-" + self.spectra_list[i].header.spectrum_type.radiometer.name + "-" + self.spectra_list[i].header.spectrum_type.optics.name)
			self.spectraListModel.appendRow(itm)

		## select first spectrum in the list for plotting
		sel_model = self.spectraListView.selectionModel()
		sel_model.setCurrentIndex(self.spectraListModel.index(0, 0), QtCore.QItemSelectionModel.SelectCurrent)


	def plot_spectrum(self):
		self.canvas.axes.cla()  # clear canvas

		## x axis 
		if self.wlScale.isChecked():
			if (self.plotted_spec.header.spectrum_type.radiometer == Radiometer.VIS):
				self.canvas.axes.plot(self.px2wl(range(self.plotted_spec.header.pixel_count), self.wlcoefs_v), self.plotted_spec.body, 'r-')
			else:
				self.canvas.axes.plot(self.px2wl(range(self.plotted_spec.header.pixel_count), self.wlcoefs_s), self.plotted_spec.body, 'r-')
			self.canvas.axes.set_xlabel("Wavelength, nm")
		else:
			self.canvas.axes.plot(range(self.plotted_spec.header.pixel_count), self.plotted_spec.body, 'r-')
			self.canvas.axes.set_xlabel("Pixel number")

		self.canvas.axes.set_aspect("auto") # autoset aspect ratio which gets messed up by the images
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


	@QtCore.pyqtSlot(QtCore.QModelIndex)
	def on_spectrumList_currentChanged(self, index):
		## header
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

		## draw the graph
		self.plot_spectrum()

		self.autoScaleY.setEnabled(True) # enable Y axis autoscale checkbox
		self.wlScale.setEnabled(True) # enable wavelength scale checkbox
		self.graphTitle.setEnabled(True) # enable graph title checkbox
		self.saveButton.setEnabled(True) # enable save button
		self.plotted_spectrum_number = index.row() + 1


	@QtCore.pyqtSlot(int)
	def on_autoScaleY_stateChanged(self, state):
		if state == 0:
			self.canvas.axes.set_ylim([0, 65535])
		else:
			self.canvas.axes.autoscale(axis='y')

		self.canvas.draw()


	@QtCore.pyqtSlot(int)
	def on_wlScale_stateChanged(self, state):
		self.canvas.axes.cla()  # clear canvas
		self.plot_spectrum()


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
