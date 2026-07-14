import sys
import clr

# NMotive assembly
clr.AddReference("NMotive")

from System import *
from NMotive import *


def ProcessTake(take, progress):

    exporter = CSVExporter()
    exporter.RotationType = Rotation.QuaternionFormat
    exporter.Units = LengthUnits.Units_Meters
    exporter.UseWorldSapceCoordinates = True
    exporter.WriteBoneMarkers = True
    exporter.WriteBones = True
    exporter.WriteHeader = True
    exporter.WriteMarkers = True
    exporter.WriteQualityStats = False
    exporter.WriteRigidBodies = True
    exporter.WriteRigidBodyMarkers = True

    # Build output path without using os module
    full_path = take.FileName
    last_sep = max(full_path.rfind('\\'), full_path.rfind('/'))
    folder = full_path[:last_sep]
    filename = full_path[last_sep + 1:]
    dot = filename.rfind('.')
    stem = filename[:dot] if dot >= 0 else filename
    output_file = folder + "\\" + stem + ".csv"

    progress.SetMessage("Exporting CSV")
    progress.SetProgress(float(0.1))
    return exporter.Export(take, output_file, True)
