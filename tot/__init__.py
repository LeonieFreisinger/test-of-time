import logging

# logger handling
log = logging.getLogger("dv")
log.setLevel("INFO")

c_handler = logging.StreamHandler()
c_handler.setLevel("WARNING")
c_format = logging.Formatter("%(levelname)s - (%(name)s.%(funcName)s) - %(message)s")
c_handler.setFormatter(c_format)
log.addHandler(c_handler)

logging.captureWarnings(True)
warnings_log = logging.getLogger("py.warnings")
warnings_log.addHandler(c_handler)

# enable write log files
write_log_file = False
if write_log_file:
    f_handler = logging.FileHandler("logs.log", "w+")
    # f_handler.setLevel("ERROR")
    f_format = logging.Formatter("%(asctime)s; %(levelname)s; %(name)s; %(funcName)s; %(message)s")
    f_handler.setFormatter(f_format)
    log.addHandler(f_handler)
    warnings_log.addHandler(f_handler)

# make version number accessible
from ._version import __version__
from .benchmark import (CrossValidationBenchmark, ManualBenchmark,
                        ManualCVBenchmark, SimpleBenchmark)
# make classes available upon package import
from .dataset import Dataset
from .experiment import CrossValidationExperiment, SimpleExperiment
from .models import (LinearRegressionModel, NaiveModel, NeuralProphetModel,
                     ProphetModel, SeasonalNaiveModel)
