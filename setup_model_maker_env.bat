@echo off
rem ─── Setup: MediaPipe Model Maker fine-tuning environment ─────────────────
rem
rem  Creates a conda env 'model_maker' with Python 3.10 + TensorFlow 2.15 +
rem  mediapipe-model-maker 0.2.1.4.
rem
rem  The main .venv uses TF 2.21 which is incompatible with Model Maker.
rem  This separate env side-steps that constraint without touching .venv.
rem
rem  After running this script once, fine-tune with:
rem    C:\Users\cladi\miniconda3\envs\model_maker\python.exe finetune_pose.py
rem ──────────────────────────────────────────────────────────────────────────

set CONDA="C:\Users\cladi\miniconda3\Scripts\conda.exe"
set ENV_NAME=model_maker
set PY_VER=3.10
set TF_VER=2.15.0
set MM_VER=0.2.1.4
set MP_VER=0.10.14

echo.
echo [1/5] Creating conda environment '%ENV_NAME%' with Python %PY_VER% ...
%CONDA% create -n %ENV_NAME% python=%PY_VER% -y
if errorlevel 1 ( echo FAILED at step 1. & exit /b 1 )

set PYEXE=C:\Users\cladi\miniconda3\envs\%ENV_NAME%\python.exe

echo [2/5] Installing TensorFlow %TF_VER% (CPU) ...
"%PYEXE%" -m pip install --upgrade pip
"%PYEXE%" -m pip install tensorflow==%TF_VER%
if errorlevel 1 ( echo FAILED at step 2. & exit /b 1 )

echo [3/5] Installing mediapipe-model-maker %MM_VER% ...
"%PYEXE%" -m pip install mediapipe-model-maker==%MM_VER%
if errorlevel 1 ( echo FAILED at step 3. & exit /b 1 )

echo [4/5] Installing mediapipe %MP_VER% (for evaluation) ...
"%PYEXE%" -m pip install "mediapipe==%MP_VER%"
if errorlevel 1 ( echo FAILED at step 4. & exit /b 1 )

echo [5/5] Installing supporting packages ...
"%PYEXE%" -m pip install opencv-python numpy pandas tqdm scikit-learn
if errorlevel 1 ( echo FAILED at step 5. & exit /b 1 )

echo.
echo ══════════════════════════════════════════════════════
echo  Environment ready.  Run fine-tuning with:
echo.
echo    C:\Users\cladi\miniconda3\envs\model_maker\python.exe finetune_pose.py
echo.
echo  Training is CPU-only on Windows.  Expected time: 3-8 h for 20 epochs.
echo  For GPU acceleration use Google Colab (see finetune_pose.py docstring).
echo ══════════════════════════════════════════════════════
