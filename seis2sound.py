#!/usr/bin/env python3
import sys
import os
import time
import csv
import io
import wave
import winsound
from datetime import datetime
import numpy as np
import serial
import serial.tools.list_ports
from collections import deque

# --- 1. FORCE SYSTEM SETTINGS ---
os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"

# --- 2. IMPORT PYQT6 & PYQTGRAPH ---
try:
    from PyQt6 import QtCore, QtWidgets, QtGui
    import pyqtgraph as pg
except ImportError:
    print("Missing libraries. Run: pip install PyQt6 pyqtgraph pyserial numpy")
    sys.exit(1)

# --- 3. GLOBAL PLOT CONFIG ---
pg.setConfigOptions(antialias=False, imageAxisOrder='row-major')

class SeismicDetector(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("STM32 Seismic Monitor (70 Hz Normalized)")
        self.resize(1280, 900)

        # --- Data Logic ---
        self.ser = None
        self.last_time = time.perf_counter()
        self.nominal_sps = 70.0  # Fixed base rate at 70 Hz
        self.current_sps = 70.0  
        self.timer_interval_ms = 20  # GUI update loop period (50 Hz)
        self.data_buffer = deque(maxlen=40000)  # Stores up to 40,000 DC-corrected values (~9.5 mins)
        self.serial_string_accumulator = "" 
        self.fft_multiplier = 1.0
        self.fft_size = 64 
        self.last_applied_nyquist = -1.0  
        
        # --- Recording Logic ---
        self.is_recording = False
        self.csv_file = None
        self.csv_writer = None
        self.recording_filepath = ""

        # --- Stream-Based DC Filter Variables ---
        self.dc_baseline = 0.0
        self.alpha_dc = 0.005  # Standard smoothing factor for baseline removal
        
        # Calibration Constant: 28V per m/s²
        self.sensitivity_v_per_ms2 = 28.0
        
        # --- GUI Layout ---
        self.central_widget = QtWidgets.QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QtWidgets.QHBoxLayout(self.central_widget)

        # Left Sidebar (Controls)
        self.sidebar = QtWidgets.QFrame()
        self.sidebar.setFixedWidth(240)
        self.sidebar.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        self.side_layout = QtWidgets.QVBoxLayout(self.sidebar)
        
        self.side_layout.addWidget(QtWidgets.QLabel("<b>CONTROL PANEL</b>"))
        self.port_selector = QtWidgets.QComboBox()
        self.refresh_ports()
        self.side_layout.addWidget(self.port_selector)
        
        self.btn_connect = QtWidgets.QPushButton("Connect")
        self.btn_connect.clicked.connect(self.toggle_connection)
        self.side_layout.addWidget(self.btn_connect)

        # --- RECORDING CONTROLS ---
        self.side_layout.addSpacing(15)
        self.side_layout.addWidget(QtWidgets.QLabel("<b>DATA RECORDING</b>"))
        self.btn_record = QtWidgets.QPushButton("Start Recording")
        self.btn_record.clicked.connect(self.toggle_recording)
        self.side_layout.addWidget(self.btn_record)

        self.lbl_record_status = QtWidgets.QLabel("Status: Idle")
        self.lbl_record_status.setWordWrap(True)
        self.side_layout.addWidget(self.lbl_record_status)

        self.side_layout.addSpacing(15)
        self.side_layout.addWidget(QtWidgets.QLabel("<b>DISPLAY DATA TYPE</b>"))
        self.unit_selector = QtWidgets.QComboBox()
        self.unit_selector.addItems(["mm/s² (Calculated)", "Raw Data Input"])
        self.unit_selector.currentIndexChanged.connect(self.update_display_settings)
        self.side_layout.addWidget(self.unit_selector)

        self.side_layout.addSpacing(15)
        self.side_layout.addWidget(QtWidgets.QLabel("<b>FFT WINDOW SIZE</b>"))
        self.fft_combo = QtWidgets.QComboBox()
        self.fft_combo.addItems(["SPS / 8", "SPS / 4", "SPS / 2", "SPS (Default)", "SPS * 2"])
        self.fft_combo.setCurrentIndex(3) 
        self.fft_combo.currentIndexChanged.connect(self.update_fft_config)
        self.side_layout.addWidget(self.fft_combo)

        # OSCILLOSCOPE MANUAL MARGINS
        self.side_layout.addSpacing(15)
        self.side_layout.addWidget(QtWidgets.QLabel("<b>OSCILLOSCOPE MARGINS</b>"))
        
        self.chk_manual_y = QtWidgets.QCheckBox("Enable Manual Y-Limits")
        self.chk_manual_y.setChecked(False)
        self.chk_manual_y.stateChanged.connect(self.apply_oscilloscope_range)
        self.side_layout.addWidget(self.chk_manual_y)
        
        self.side_layout.addWidget(QtWidgets.QLabel("Max Amplitude (+/- Raw Counts):"))
        self.spin_y_amplitude = QtWidgets.QSpinBox()
        self.spin_y_amplitude.setRange(1, 8388608)  # Positive absolute amplitude limit
        self.spin_y_amplitude.setValue(5000)
        self.spin_y_amplitude.setSingleStep(500)
        self.spin_y_amplitude.valueChanged.connect(self.apply_oscilloscope_range)
        self.side_layout.addWidget(self.spin_y_amplitude)

        # HARDWARE DC CORRECTION TRACKING DISPLAY
        self.side_layout.addSpacing(15)
        self.side_layout.addWidget(QtWidgets.QLabel("<b>DC BASELINE ENGINE</b>"))
        self.chk_dc_enable = QtWidgets.QCheckBox("Enable DC Correction")
        self.chk_dc_enable.setChecked(True)  
        self.side_layout.addWidget(self.chk_dc_enable)
        
        self.lbl_dc_val = QtWidgets.QLabel("Current Bias: 0.00")
        self.side_layout.addWidget(self.lbl_dc_val)

        # --- SEISMIC SONIFICATION CONTROLS ---
        self.side_layout.addSpacing(15)
        self.side_layout.addWidget(QtWidgets.QLabel("<b>SEISMIC SONIFICATION</b>"))
        
        self.combo_speedup = QtWidgets.QComboBox()
        self.combo_speedup.addItems([
            "5x Speedup", 
            "12x Speedup", 
            "25x Speedup", 
            "50x Speedup (Default)", 
            "100x Speedup"
        ])
        self.combo_speedup.setCurrentIndex(3)  # Default to 200x
        self.side_layout.addWidget(self.combo_speedup)

        # Audio Volume Regulation Control
        self.lbl_volume = QtWidgets.QLabel("Volume: 80%")
        self.side_layout.addWidget(self.lbl_volume)
        self.slider_volume = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.slider_volume.setRange(0, 100)
        self.slider_volume.setValue(80)
        self.slider_volume.valueChanged.connect(lambda v: self.lbl_volume.setText(f"Volume: {v}%"))
        self.side_layout.addWidget(self.slider_volume)

        self.btn_play_audio = QtWidgets.QPushButton("🔊 Listen to Buffer")
        self.btn_play_audio.clicked.connect(self.play_sonified_audio)
        self.side_layout.addWidget(self.btn_play_audio)

        self.side_layout.addSpacing(15)
        self.status_sps = QtWidgets.QLabel("Auto-Measured SPS: ---")
        self.side_layout.addWidget(self.status_sps)
        self.status_id = QtWidgets.QLabel("Last ID: ---")
        self.side_layout.addWidget(self.status_id)
        
        self.side_layout.addStretch()
        
        # Right Side Visuals
        self.plot_container = QtWidgets.QWidget()
        self.plot_layout = QtWidgets.QVBoxLayout(self.plot_container)
        self.plot_layout.setContentsMargins(0, 0, 0, 0)
        
        self.pg_widget = pg.GraphicsLayoutWidget()
        self.plot_layout.addWidget(self.pg_widget)

        self.main_layout.addWidget(self.sidebar)
        self.main_layout.addWidget(self.plot_container, stretch=1)

        # --- Setup Plots ---
        self.p1 = self.pg_widget.addPlot(title="Seismic Waveform")
        self.curve = self.p1.plot(pen='y')
        self.p1.showGrid(x=True, y=True)
        self.p1.setLabel('bottom', 'Time', units='s')
        self.p1.disableAutoRange(axis=pg.ViewBox.XAxis)
        
        self.pg_widget.nextRow()
        
        self.p2 = self.pg_widget.addPlot(title="Spectrogram")
        self.img = pg.ImageItem()
        self.img.setOpts(axisOrder='row-major') 
        self.p2.addItem(self.img)
        self.p2.setLabel('left', 'Frequency', units='Hz')
        self.p2.setLabel('bottom', 'Time', units='s')
        
        self.spec_width = 600 
        self.update_fft_config()
        self.init_spectrogram() 
        self.update_plot_labels() 
        
        pos = np.array([0.0, 1.0])
        color = np.array([[0, 0, 0, 255], [255, 255, 255, 255]], dtype=np.ubyte)
        cmap = pg.ColorMap(pos, color)
        self.img.setLookupTable(cmap.getLookupTable())

        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update_gui)
        self.timer.start(self.timer_interval_ms)

    def init_spectrogram(self):
        num_bins = (self.fft_size // 2) + 1
        self.spectrogram_data = np.zeros((num_bins, self.spec_width))
        self.img.setImage(self.spectrogram_data, autoLevels=True)
        self.last_applied_nyquist = -1.0  

    def update_plot_labels(self):
        suffix = " (DC Corrected)" if self.chk_dc_enable.isChecked() else ""
        if self.unit_selector.currentIndex() == 0:
            self.p1.setTitle(f"Acceleration Waveform{suffix}")
            self.p1.setLabel('left', 'Acceleration', units='mm/s²')
            self.p2.setTitle(f"Auto-Scaling Spectrogram (mm/s²){suffix}")
        else:
            self.p1.setTitle(f"Raw Input Waveform{suffix}")
            self.p1.setLabel('left', 'Amplitude', units='Units')
            self.p2.setTitle(f"Auto-Scaling Spectrogram (Units){suffix}")

    def apply_oscilloscope_range(self):
        if self.chk_manual_y.isChecked():
            amplitude = self.spin_y_amplitude.value()
            raw_min = -amplitude
            raw_max = amplitude

            if self.unit_selector.currentIndex() == 0:
                y_min = (raw_min / self.sensitivity_v_per_ms2) * 1000.0
                y_max = (raw_max / self.sensitivity_v_per_ms2) * 1000.0
            else:
                y_min = raw_min
                y_max = raw_max
                
            self.p1.setYRange(y_min, y_max, padding=0)
        else:
            self.p1.enableAutoRange(axis=pg.ViewBox.YAxis)

    def update_display_settings(self):
        self.update_plot_labels()
        self.apply_oscilloscope_range()

    def update_fft_config(self):
        mapping = [0.125, 0.25, 0.5, 1.0, 2.0]
        self.fft_multiplier = mapping[self.fft_combo.currentIndex()]
        raw_size = self.nominal_sps * self.fft_multiplier
        # Snap FFT size to nearest power of 2 for maximum performance
        self.fft_size = max(32, 2 ** int(np.round(np.log2(raw_size))))
        self.init_spectrogram()

    def refresh_ports(self):
        self.port_selector.clear()
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_selector.addItems(ports)

    def toggle_connection(self):
        if self.ser is None or not self.ser.is_open:
            try:
                port = self.port_selector.currentText()
                if not port: return
                self.ser = serial.Serial(port, 500000, timeout=0.01)
                self.ser.reset_input_buffer()
                self.serial_string_accumulator = "" 
                self.data_buffer.clear()
                self.dc_baseline = 0.0  
                self.update_fft_config()
                self.apply_oscilloscope_range()
                self.btn_connect.setText("Disconnect")
                self.btn_connect.setStyleSheet("background-color: #c0392b; color: white;")
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "Error", f"Could not connect: {e}")
        else:
            self.stop_recording()
            if self.ser: self.ser.close()
            self.btn_connect.setText("Connect")
            self.btn_connect.setStyleSheet("")

    # --- RECORDING FUNCTIONS ---
    def toggle_recording(self):
        if not self.is_recording:
            self.start_recording()
        else:
            self.stop_recording()

    def start_recording(self):
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            filename = time.strftime("%Y-%m-%d-%H-%M-%S") + ".csv"
            self.recording_filepath = os.path.join(script_dir, filename)

            self.csv_file = open(self.recording_filepath, mode='w', newline='', encoding='utf-8')
            self.csv_writer = csv.writer(self.csv_file)
            
            # CSV Headers
            self.csv_writer.writerow(["Timestamp_s", "Packet_ID", "Raw_Input", "DC_Corrected", "Acceleration_mm_s2"])
            self.csv_file.flush()

            self.is_recording = True
            self.btn_record.setText("Stop Recording")
            self.btn_record.setStyleSheet("background-color: #e74c3c; color: white; font-weight: bold;")
            self.lbl_record_status.setText(f"<b>Rec:</b> {filename}")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Recording Error", f"Could not start recording: {e}")

    def stop_recording(self):
        if self.is_recording:
            self.is_recording = False
            if self.csv_file:
                try:
                    self.csv_file.flush()
                    self.csv_file.close()
                except Exception as e:
                    print(f"Error closing CSV file: {e}")
                self.csv_file = None
                self.csv_writer = None

            self.btn_record.setText("Start Recording")
            self.btn_record.setStyleSheet("")
            self.lbl_record_status.setText("Status: Saved")

    # --- AUDIO SONIFICATION ENGINE ---
    def play_sonified_audio(self):
        """Upscales stored low-frequency seismic buffer into audible sound with volume scaling."""
        if len(self.data_buffer) < 140:
            return  # Need at least 2 seconds of buffer data

        # 1. Get speedup mapping (25x, 50x, 100x, 200x, 400x)
        speedup_map = [5.0, 12.0, 25.0, 50.0, 100.0]
        speedup = speedup_map[self.combo_speedup.currentIndex()]

        # 2. Extract buffer history (up to 40,000 points)
        data = np.array(self.data_buffer, dtype=float)

        # 3. Resampling math (Compress time to shift frequency up)
        target_audio_sr = 44100  # Standard soundcard sample rate (44.1 kHz)
        orig_duration = len(data) / self.nominal_sps
        sped_up_duration = orig_duration / speedup
        num_audio_samples = int(sped_up_duration * target_audio_sr)

        if num_audio_samples < 100:
            return

        # 4. Grid interpolation
        t_orig = np.linspace(0, orig_duration, len(data))
        t_audio = np.linspace(0, orig_duration, num_audio_samples)
        resampled_data = np.interp(t_audio, t_orig, data)

        # 5. Volume Regulation Math
        vol_factor = self.slider_volume.value() / 100.0  # Scale between 0.0 and 1.0
        max_amplitude = np.max(np.abs(resampled_data))
        
        if max_amplitude > 0:
            # Scale amplitude based on volume slider setting
            resampled_data = (resampled_data / max_amplitude) * (32000.0 * vol_factor)
        pcm_16bit_data = resampled_data.astype(np.int16)

        # 6. Save temporary WAV file
        temp_wav = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp_seismic.wav")
        try:
            with wave.open(temp_wav, 'wb') as wf:
                wf.setnchannels(1)                # Mono
                wf.setsampwidth(2)               # 16-bit depth
                wf.setframerate(target_audio_sr)  # 44.1 kHz
                wf.writeframes(pcm_16bit_data.tobytes())

            # 7. Play non-blocking sound using native Windows audio driver
            winsound.PlaySound(temp_wav, winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception as e:
            print(f"Audio playback error: {e}")

    def update_gui(self):
        if self.ser and self.ser.is_open:
            if self.ser.in_waiting > 0:
                try:
                    chunk = self.ser.read(self.ser.in_waiting).decode('utf-8', errors='ignore')
                    self.serial_string_accumulator += chunk
                    
                    lines = self.serial_string_accumulator.split('\n')
                    self.serial_string_accumulator = lines.pop()
                    
                    new_samples = 0
                    last_id = "---"
                    
                    parsed_values = []
                    records_to_write = []

                    for line in lines:
                        parts = line.strip().split(',')
                        if len(parts) >= 4:
                            try:
                                last_id = parts[0]
                                raw_val = float(parts[3])
                                
                                # Continuous baseline tracker
                                if self.dc_baseline == 0.0 and len(self.data_buffer) == 0:
                                    self.dc_baseline = raw_val  
                                else:
                                    self.dc_baseline = (self.alpha_dc * raw_val) + ((1.0 - self.alpha_dc) * self.dc_baseline)
                                
                                corrected_val = (raw_val - self.dc_baseline) if self.chk_dc_enable.isChecked() else raw_val
                                    
                                parsed_values.append(corrected_val)
                                new_samples += 1

                                # Prepare data row for CSV if recording
                                if self.is_recording:
                                    sys_ts = time.time()
                                    accel_val = (corrected_val / self.sensitivity_v_per_ms2) * 1000.0
                                    records_to_write.append([
                                        f"{sys_ts:.4f}", 
                                        last_id, 
                                        raw_val, 
                                        corrected_val, 
                                        accel_val
                                    ])

                            except (ValueError, IndexError):
                                continue 
                    
                    # Batch write to CSV file without forcing expensive disk flush
                    if self.is_recording and records_to_write and self.csv_writer:
                        self.csv_writer.writerows(records_to_write)

                    if new_samples > 0:
                        self.data_buffer.extend(parsed_values)
                        self.lbl_dc_val.setText(f"Current Bias: {self.dc_baseline:.2f}")
                        
                        # Auto-Measure Sample Rate
                        now = time.perf_counter()
                        dt = now - self.last_time
                        if dt > 0:
                            measured = new_samples / dt
                            self.current_sps = (self.current_sps * 0.95) + (measured * 0.05)
                            self.last_time = now
                            self.status_sps.setText(f"Auto-Measured: {int(self.current_sps)} Hz")
                            self.status_id.setText(f"ID: {last_id}")

                        # Dynamic history window (fixed 2-second view at 70 Hz = 140 samples)
                        view_pts = int(self.nominal_sps * 2) 
                        processed_history = list(self.data_buffer)
                        
                        if len(processed_history) >= view_pts:
                            display_slice = np.array(processed_history[-view_pts:], dtype=float)
                            
                            # Scaling factor for Waveform Display
                            if self.unit_selector.currentIndex() == 0:
                                display_slice = (display_slice / self.sensitivity_v_per_ms2) * 1000.0
                                
                            time_axis = np.arange(len(display_slice)) / self.nominal_sps
                            
                            self.curve.setData(x=time_axis, y=display_slice)
                            self.p1.setXRange(0, 2.0, padding=0)

                            if self.chk_manual_y.isChecked():
                                self.apply_oscilloscope_range()

                            # --- SPECTROGRAM PROCESSING ---
                            if len(processed_history) >= self.fft_size:
                                segment = np.array(processed_history[-self.fft_size:], dtype=float)
                                
                                if self.unit_selector.currentIndex() == 0:
                                    segment = (segment / self.sensitivity_v_per_ms2) * 1000.0
                                    
                                fft_mag = np.abs(np.fft.rfft(segment * np.hanning(self.fft_size)))
                                
                                if fft_mag.shape[0] == self.spectrogram_data.shape[0]:
                                    self.spectrogram_data = np.roll(self.spectrogram_data, -1, axis=1)
                                    self.spectrogram_data[:, -1] = fft_mag
                                    self.img.setImage(self.spectrogram_data, autoLevels=True)
                                    
                                    nyquist = self.nominal_sps / 2.0
                                    spec_duration_sec = self.spec_width * (self.timer_interval_ms / 1000.0)
                                    
                                    if abs(nyquist - self.last_applied_nyquist) > 0.01:
                                        self.img.setRect(QtCore.QRectF(0, 0, spec_duration_sec, nyquist))
                                        self.last_applied_nyquist = nyquist

                except Exception as e:
                    print(f"Data engine runtime error: {e}")

    def closeEvent(self, event):
        self.stop_recording()
        if self.ser: self.ser.close()

        # Cleanup temporary audio file if present
        temp_wav = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp_seismic.wav")
        if os.path.exists(temp_wav):
            try:
                os.remove(temp_wav)
            except Exception:
                pass
                
        event.accept()

if __name__ == '__main__':
    try:
        app = QtWidgets.QApplication(sys.argv)
        app.setStyle('Fusion')
        win = SeismicDetector()
        win.show()
        sys.exit(app.exec())
    except Exception as e:
        print(f"CRITICAL CRASH: {e}")