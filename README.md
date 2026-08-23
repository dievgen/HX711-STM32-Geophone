# STM32 Seismic Monitor & Sonification Suite

An end-to-end telemetry system designed to capture, visualize, record, and sonify low-frequency seismic activity. The system pairs custom STM32 microsecond-accurate sensor acquisition firmware with a responsive desktop GUI.

## Key Features

* **High-Speed STM32 Firmware:** Bit-bangs 24-bit sensor channels (e.g., `PB8 DOUT` / `PB9 SCK`) with 64x gain hardware timing guards and streams 4-element CSV frames over 500,000 baud serial.
* **Real-time Visualization:** Built on `PyQt6` and `pyqtgraph` to render low-latency time-domain acceleration waveforms ($\text{mm/s}^2$) and dynamic auto-scaling spectrograms.
* **Seismic Sonification Engine:** Resamples and compresses low-frequency seismic buffer history into audible frequencies using adjustable speedup multipliers ($5\times$ to $100\times$) and 16-bit PCM WAV playback.
* **Continuous DC Baseline Tracking:** Implements an inline exponential smoothing filter ($\alpha = 0.005$) to dynamically track and eliminate DC bias drift in real time.
* **Data Telemetry Logging:** Background CSV recording engine that logs Unix timestamps, packet IDs, raw ADC counts, DC-corrected values, and calibrated acceleration.
* **Oscilloscope & FFT Controls:** Adjustable power-of-two FFT window sizes, auto-calculated Nyquist bounds, auto-measured sample rates (~70 Hz nominal), and manual Y-axis amplitude scaling.

## System Architecture

* **Hardware / Firmware (`Infrasound Lab v17.3`):** Interfaces with the 24-bit sensor on STM32 pins `PB8`/`PB9`, handles Two's Complement sign extensions, and outputs high-throughput serial frames.
* **Desktop GUI (`seis2sound.py`):** Python application that manages the serial accumulator, dynamic ring buffers (up to 40,000 samples), digital filtering, spectral transformations, and audio rendering.

## Tech Stack

* **Firmware:** C++ / STM32 (Arduino core)
* **GUI & Plotting:** Python 3, PyQt6, pyqtgraph
* **Signal Processing & Audio:** NumPy, PySerial, Wave, Winsound

Quick Start
Flash the firmware onto your STM32 target.  
Install Python dependencies:
```bash
# Clone the repository and install requirements
git clone [https://github.com/your-username/stm32-seismic-monitor.git](https://github.com/your-username/stm32-seismic-monitor.git)
cd stm32-seismic-monitor
pip install PyQt6 pyqtgraph pyserial numpy
```
