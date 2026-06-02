from setuptools import setup, find_packages

setup(
    name="pendulastic",
    version="0.1.0",
    description="Markerless pendulum test analysis for lower-limb spasticity quantification in MS",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    python_requires=">=3.10",
    install_requires=[
        "opencv-python>=4.9.0",
        "mediapipe>=0.10.14",
        "numpy>=1.26.0",
        "scipy>=1.13.0",
        "pandas>=2.2.0",
        "matplotlib>=3.9.0",
        "scikit-learn>=1.5.0",
    ],
)
