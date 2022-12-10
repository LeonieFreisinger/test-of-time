import logging
from abc import ABC, abstractmethod
from copy import copy, deepcopy
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Type

import numpy as np
import pandas as pd
from neuralprophet import NeuralProphet, df_utils

from tot.utils import _get_seasons, convert_df_to_TimeSeries, convert_to_datetime

# check import of implemented models and consider order of imports
try:
    from sklearn.linear_model import LinearRegression

    _sklearn_installed = True
except ImportError:
    LinearRegression = None
    _sklearn_installed = False
    raise ImportError(
        "The LinearRegression model could not be imported."
        "Check for proper installation of sklearn: https://scikit-learn.org/stable/install.html"
    )

try:
    from darts.models import RegressionModel

    _darts_installed = True
except ImportError:
    RegressionModel = None
    _darts_installed = False
    raise ImportError(
        "The RegressionModel could not be imported."
        "Check for proper installation of darts: https://github.com/unit8co/darts/blob/master/INSTALL.md"
    )

try:
    from prophet import Prophet

    _prophet_installed = True
except ImportError:
    Prophet = None
    _prophet_installed = False
    raise ImportError(
        "The Prophet model could not be imported."
        "Check for proper installation of prophet: https://facebook.github.io/prophet/docs/installation.html"
    )

log = logging.getLogger("tot.model")


@dataclass
class Model(ABC):
    """
    example use:
    >>> models = []
    >>> for params in [{"n_changepoints": 5}, {"n_changepoints": 50},]:
    >>>     models.append(Model(
    >>>         params=params
    >>>         model_name="NeuralProphet",
    >>>         model_class=NeuralProphet,
    >>>     ))
    """

    params: dict
    model_name: str
    model_class: Type

    @abstractmethod
    def fit(self, df: pd.DataFrame, freq: str):
        pass

    @abstractmethod
    def predict(self, df: pd.DataFrame):
        pass

    def _handle_missing_data(self, df, freq, predicting=False):
        """
        if Model does not provide own data handling method: handles missing data
        else (time-features only): returns unchanged df
        """
        return df

    def maybe_add_first_inputs_to_df(self, df_train, df_test):
        """
        if Model with lags: adds n_lags values to start of df_test.
        else (time-features only): returns unchanged df_test
        """
        return df_test.reset_index(drop=True)

    def maybe_drop_first_forecasts(self, predicted, df):
        """
        if Model with lags: removes first n_lags values from predicted and df_test
        else (time-features only): returns unchanged df_test
        """
        return predicted.reset_index(drop=True), df.reset_index(drop=True)

    def maybe_drop_added_dates(self, predicted, df):
        """if Model imputed any dates: removes any dates in predicted which are not in df_test."""
        return predicted.reset_index(drop=True), df.reset_index(drop=True)


@dataclass
class ProphetModel(Model):
    model_name: str = "Prophet"
    model_class: Type = Prophet

    def __post_init__(self):
        if not _prophet_installed:
            raise RuntimeError("Requires prophet to be installed")
        data_params = self.params["_data_params"]
        custom_seasonalities = None
        if "seasonalities" in data_params and len(data_params["seasonalities"]) > 0:
            daily, weekly, yearly, custom_seasonalities = _get_seasons(data_params["seasonalities"])
            self.params.update({"daily_seasonality": daily})
            self.params.update({"weekly_seasonality": weekly})
            self.params.update({"yearly_seasonality": yearly})
        model_params = deepcopy(self.params)
        model_params.pop("_data_params")
        self.model = self.model_class(**model_params)
        if custom_seasonalities is not None:
            for seasonality in custom_seasonalities:
                self.model.add_seasonality(name="{}_daily".format(str(seasonality)), period=seasonality)
        self.n_forecasts = 1  # adjust as _pred_param
        self.n_lags = 0

    def fit(self, df: pd.DataFrame, freq: str):
        if "ID" in df.columns and len(df["ID"].unique()) > 1:
            raise NotImplementedError("Prophet does not work with many ts df")
        self.freq = freq
        self.model = self.model.fit(df=df)

    def predict(self, df: pd.DataFrame):
        fcst = self.model.predict(df=df)
        fcst_df = pd.DataFrame({"time": fcst.ds, "y": df.y, "yhat1": fcst.yhat})
        return fcst_df


@dataclass
class NeuralProphetModel(Model):
    model_name: str = "NeuralProphet"
    model_class: Type = NeuralProphet

    def __post_init__(self):
        data_params = self.params["_data_params"]
        custom_seasonalities = None
        if "seasonalities" in data_params and len(data_params["seasonalities"]) > 0:
            daily, weekly, yearly, custom_seasonalities = _get_seasons(data_params["seasonalities"])
            self.params.update({"daily_seasonality": daily})
            self.params.update({"weekly_seasonality": weekly})
            self.params.update({"yearly_seasonality": yearly})
        if "seasonality_mode" in data_params and data_params["seasonality_mode"] is not None:
            self.params.update({"seasonality_mode": data_params["seasonality_mode"]})
        model_params = deepcopy(self.params)
        model_params.pop("_data_params")
        self.model = self.model_class(**model_params)
        if custom_seasonalities is not None:
            for seasonality in custom_seasonalities:
                self.model.add_seasonality(name="{}_daily".format(str(seasonality)), period=seasonality)
        self.n_forecasts = self.model.n_forecasts  # adjust as _pred_param
        self.n_lags = self.model.n_lags

    def fit(self, df: pd.DataFrame, freq: str):
        self.freq = freq
        _ = self.model.fit(df=df, freq=freq, progress="none", minimal=True)

    def predict(self, df: pd.DataFrame):
        fcst = self.model.predict(df=df)
        fcst, received_ID_col, received_single_time_series, received_dict, _ = df_utils.prep_or_copy_df(fcst)
        fcst_df = pd.DataFrame()
        for df_name, fcst_i in fcst.groupby("ID"):
            y_cols = ["y"] + [col for col in fcst_i.columns if "yhat" in col]
            fcst_aux = pd.DataFrame({"time": fcst_i.ds})
            for y_col in y_cols:
                fcst_aux[y_col] = fcst_i[y_col]
            fcst_aux["ID"] = df_name
            fcst_df = pd.concat((fcst_df, fcst_aux), ignore_index=True)
        fcst_df = df_utils.return_df_in_original_format(
            fcst_df, received_ID_col, received_single_time_series, received_dict
        )
        return fcst_df

    def maybe_add_first_inputs_to_df(self, df_train, df_test):
        """Adds last n_lags values from df_train to start of df_test."""
        if self.model.n_lags > 0:
            df_train, _, _, _, _ = df_utils.prep_or_copy_df(df_train)
            (
                df_test,
                received_ID_col_test,
                received_single_time_series_test,
                received_dict_test,
                _,
            ) = df_utils.prep_or_copy_df(df_test)
            df_test_new = pd.DataFrame()
            for df_name, df_test_i in df_test.groupby("ID"):
                df_train_i = df_train[df_train["ID"] == df_name].copy(deep=True)
                df_test_i = pd.concat([df_train_i.tail(self.model.n_lags), df_test_i], ignore_index=True)
                df_test_new = pd.concat((df_test_new, df_test_i), ignore_index=True)
            df_test = df_utils.return_df_in_original_format(
                df_test_new, received_ID_col_test, received_single_time_series_test, received_dict_test
            )
        return df_test

    def maybe_drop_first_forecasts(self, predicted, df):
        """
        if Model with lags: removes first n_lags values from predicted and df
        else (time-features only): returns unchanged df
        """
        if self.model.n_lags > 0:
            (
                predicted,
                received_ID_col_pred,
                received_single_time_series_pred,
                received_dict_test_pred,
                _,
            ) = df_utils.prep_or_copy_df(predicted)
            df, received_ID_col_df, received_single_time_series_df, received_dict_test_df, _ = df_utils.prep_or_copy_df(
                df
            )
            predicted_new = pd.DataFrame()
            df_new = pd.DataFrame()
            for df_name, df_i in df.groupby("ID"):
                predicted_i = predicted[predicted["ID"] == df_name].copy(deep=True)
                predicted_i = predicted_i[self.model.n_lags :]
                df_i = df_i[self.model.n_lags :]
                df_new = pd.concat((df_new, df_i), ignore_index=True)
                predicted_new = pd.concat((predicted_new, predicted_i), ignore_index=True)
            df = df_utils.return_df_in_original_format(
                df_new, received_ID_col_df, received_single_time_series_df, received_dict_test_df
            )
            predicted = df_utils.return_df_in_original_format(
                predicted_new, received_ID_col_pred, received_single_time_series_pred, received_dict_test_pred
            )
        return predicted, df

    def maybe_drop_added_dates(self, predicted, df):
        """if Model imputed any dates: removes any dates in predicted which are not in df_test."""
        (
            predicted,
            received_ID_col_pred,
            received_single_time_series_pred,
            received_dict_test_pred,
            _,
        ) = df_utils.prep_or_copy_df(predicted)
        df, received_ID_col_df, received_single_time_series_df, received_dict_test_df, _ = df_utils.prep_or_copy_df(df)
        predicted_new = pd.DataFrame()
        df_new = pd.DataFrame()
        for df_name, df_i in df.groupby("ID"):
            predicted_i = predicted[predicted["ID"] == df_name].copy(deep=True)
            df_i["ds"] = convert_to_datetime(df_i["ds"])
            df_i.set_index("ds", inplace=True)
            predicted_i.set_index("time", inplace=True)
            predicted_i = predicted_i.loc[df_i.index]
            predicted_i = predicted_i.reset_index()
            df_i = df_i.reset_index()
            df_new = pd.concat((df_new, df_i), ignore_index=True)
            predicted_new = pd.concat((predicted_new, predicted_i), ignore_index=True)
        df = df_utils.return_df_in_original_format(
            df_new, received_ID_col_df, received_single_time_series_df, received_dict_test_df
        )
        predicted = df_utils.return_df_in_original_format(
            predicted_new, received_ID_col_pred, received_single_time_series_pred, received_dict_test_pred
        )
        return predicted, df


@dataclass
class LinearRegressionModel(Model):
    """
     A forecasting model using a linear regression of some of the target series' lags, as well as optionally some
     covariate series lags in order to obtain a forecast.

     Parameters
     ----------
         n_lags : int
             Lagged target values used to predict the next time step. For integers is given the last `lags` past lags
             are used (from -1 backward).
         output_chunk_length : int
             Number of time steps predicted at once by the internal regression model. Does not have to equal the forecast
             horizon `n` used in `predict()`. However, setting `output_chunk_length` equal to the forecast horizon may
             be useful if the covariates don't extend far enough into the future.
         model : Type
             Scikit-learn-like model with ``fit()`` and ``predict()`` methods. Also possible to use model that doesn't
             support multi-output regression for multivariate timeseries, in which case one regressor
             will be used per component in the multivariate series.
             If None, defaults to: ``sklearn.linear_model.LinearRegression(n_jobs=-1)``.
         multi_models : bool
             If True, a separate model will be trained for each future lag to predict. If False, a single model is
             trained to predict at step 'output_chunk_length' in the future. Default: True.

     Examples
     --------
     >>> model_classes_and_params = [
     >>>     (
     >>>         LinearRegressionModel,
     >>>         {"lags": 12, "output_chunk_length": 4, "n_forecasts": 4},
     >>>     ),
     >>> ]
     >>>
     >>> benchmark = SimpleBenchmark(
     >>>     model_classes_and_params=model_classes_and_params,
     >>>     datasets=dataset_list,
     >>>     metrics=list(ERROR_FUNCTIONS.keys()),
     >>>     test_percentage=25,
     >>>     save_dir=SAVE_DIR,
     >>>     num_processes=1,
     >>> )

     Note
     ----
     COMMENT
     Supported capabilities
     COMMENT
         * univariate time series
         * single target
         * autoregression, n_lags > 1
         * n_forecats > 1
         * output_chunk_length > 1

     COMMENT
     Not supported capabilities
     COMMENT
         * probabilitstic forecast
         * add_encoders

    COMMENT
    Planned capabilities
    COMMENT
         * multi-target
         * past covariates
         * future covariates
         * frequency check and optional frequency conversion
    """

    model_name: str = "LinearRegressionModel"
    model_class: Type = RegressionModel

    def __post_init__(self):
        # check if installed
        if not (_darts_installed or _sklearn_installed):
            raise RuntimeError(
                "Requires darts and sklearn to be installed:"
                "https://scikit-learn.org/stable/install.html"
                "https://github.com/unit8co/darts/blob/master/INSTALL.md"
            )
        # translate to model variable naming conventions if necessary
        self.params["lags"] = self.params.pop("n_lags")
        # assign model
        data_params = self.params["_data_params"]
        model_params = deepcopy(self.params)
        model_params.pop("_data_params")
        model_params.pop("n_forecasts")
        model = LinearRegression(n_jobs=-1)  # n_jobs=-1 indicates to use all processors
        model_params.update({"model": model})
        self.model = self.model_class(**model_params)
        self.n_forecasts = self.params["n_forecasts"]
        self.n_lags = model_params["lags"]

        # input checks are provided by model itself

    def fit(self, df: pd.DataFrame, freq: str):
        """Fits the regression model.

        Parameters
        ----------
            df : pd.DataFrame
                dataframe containing column ``ds``, ``y``, and optionally ``ID`` with all data
            freq : str
                frequency of the input data
        """
        self.freq = freq
        series = convert_df_to_TimeSeries(df, value_cols=df.columns.values[1:-1].tolist(), freq=self.freq)
        self.model = self.model.fit(series)

    def predict(self, df: pd.DataFrame):
        """Runs the model to make predictions.

        Expects all data to be present in dataframe.

        Parameters
        ----------
            df : pd.DataFrame
                dataframe containing column ``ds``, ``y``, and optionally ``ID`` with data

        Returns
        -------
            pd.DataFrame
                columns ``ds``, ``y``, optionally [``ID``], and [``yhat<i>``] where yhat<i> refers to the
                i-step-ahead prediction for this row's datetime, e.g. yhat3 is the prediction for this datetime,
                predicted 3 steps ago, "3 steps old".
        """
        df, received_ID_col, received_single_time_series, received_dict, _ = df_utils.prep_or_copy_df(df)
        # Receives df with single ID column. Only single time series accepted.
        assert received_ID_col
        series = convert_df_to_TimeSeries(df, value_cols=df.columns.values[1:-1].tolist(), freq=self.freq)
        predicted_list = self.model.historical_forecasts(
            series,
            start=self.n_lags,
            forecast_horizon=self.n_forecasts,
            retrain=False,
            last_points_only=False,
            verbose=True,
        )
        # convert TimeSeries to np.array
        prediction_series = [prediction_series.values() for i, prediction_series in enumerate(predicted_list)]
        predicted_array = np.stack(prediction_series, axis=0).squeeze()

        fcst_df = self._reshape_raw_predictions_to_forecst_df(df, predicted_array)
        return fcst_df

    def _reshape_raw_predictions_to_forecst_df(self, df_i, predicted):  # Todo outsource to df_utils?
        """Turns forecast-origin-wise predictions into forecast-target-wise predictions.
        Parameters
        ----------
            df : pd.DataFrame
                input dataframe
            predicted : np.array
                Array containing the predictions
        Returns
        -------
            pd.DataFrame
                columns ``ds``, ``y``, optionally ``ID`` and [``yhat<i>``],
                Note
                ----
                where yhat<i> refers to the i-step-ahead prediction for this row's datetime.
                e.g. yhat3 is the prediction for this datetime, predicted 3 steps ago, "3 steps old".
        """
        cols = ["ds", "y", "ID"]  # cols to keep from df
        fcst_df = pd.concat((df_i[cols],), axis=1)
        # create a line for each forecast_lag
        # 'yhat<i>' is the forecast for 'y' at 'ds' from i steps ago.
        for forecast_lag in range(1, self.n_forecasts + 1):
            forecast = predicted[:, forecast_lag - 1]
            pad_before = self.n_lags + forecast_lag - 1
            pad_after = self.n_forecasts - forecast_lag
            yhat = np.concatenate(
                ([np.NaN] * pad_before, forecast, [np.NaN] * pad_after)
            )  # add pad based on n_forecasts and current forecast_lag
            name = f"yhat{forecast_lag}"
            fcst_df[name] = yhat

        return fcst_df

    def __handle_missing_data(self, df, freq, predicting):
        """Checks and normalizes new data

        Data is also auto-imputed, since impute_missing is manually set to ``True``.

        Parameters
        ----------
            df : pd.DataFrame
                dataframe containing column ``ds``, ``y`` with all data
            freq : str
                data step sizes. Frequency of data recording,

                Note
                ----
                Any valid frequency for pd.date_range, such as ``5min``, ``D``, ``MS`` or ``auto`` (default) to automatically set frequency.
            predicting : bool
                when no lags, allow NA values in ``y`` of forecast series or ``y`` to miss completely

        Returns
        -------
            pd.DataFrame
                preprocessed dataframe
        """
        # Receives df with single ID column
        assert len(df["ID"].unique()) == 1
        if self.n_lags == 0 and not predicting:
            # we can drop rows with NA in y
            sum_na = sum(df["y"].isna())
            if sum_na > 0:
                df = df[df["y"].notna()]
                log.info(f"dropped {sum_na} NAN row in 'y'")
        # Set impute_missing manually to True
        impute_missing = True

        # add missing dates for autoregression modelling
        if self.n_lags > 0:
            df, missing_dates = df_utils.add_missing_dates_nan(df, freq=freq)
            if missing_dates > 0:
                if impute_missing:
                    log.info(f"{missing_dates} missing dates added.")
                # FIX Issue#52
                # Comment error raising to allow missing data for autoregression flow.
                # else:
                #     raise ValueError(f"{missing_dates} missing dates found. Please preprocess data manually or set impute_missing to True.")
                # END FIX

        # if self.config_regressors is not None:
        #     # if future regressors, check that they are not nan at end, else drop
        #     # we ignore missing events, as those will be filled in with zeros.
        #     reg_nan_at_end = 0
        #     for col, regressor in self.config_regressors.items():
        #         # check for completeness of the regressor values
        #         col_nan_at_end = 0
        #         while len(df) > col_nan_at_end and df[col].isnull().iloc[-(1 + col_nan_at_end)]:
        #             col_nan_at_end += 1
        #         reg_nan_at_end = max(reg_nan_at_end, col_nan_at_end)
        #     if reg_nan_at_end > 0:
        #         # drop rows at end due to missing future regressors
        #         df = df[:-reg_nan_at_end]
        #         log.info(f"Dropped {reg_nan_at_end} rows at end due to missing future regressor values.")

        df_end_to_append = None
        nan_at_end = 0
        while len(df) > nan_at_end and df["y"].isnull().iloc[-(1 + nan_at_end)]:
            nan_at_end += 1
        if nan_at_end > 0:
            if predicting:
                # allow nans at end - will re-add at end
                if self.n_forecasts > 1 and self.n_forecasts < nan_at_end:
                    # check that not more than n_forecasts nans, else drop surplus
                    df = df[: -(nan_at_end - self.n_forecasts)]
                    # correct new length:
                    nan_at_end = self.n_forecasts
                    log.info(
                        "Detected y to have more NaN values than n_forecast can predict. "
                        f"Dropped {nan_at_end - self.n_forecasts} rows at end."
                    )
                df_end_to_append = df[-nan_at_end:]
                df = df[:-nan_at_end]
            else:
                # training - drop nans at end
                df = df[:-nan_at_end]
                log.info(
                    f"Dropped {nan_at_end} consecutive nans at end. "
                    "Training data can only be imputed up to last observation."
                )

        # impute missing values
        data_columns = []
        if self.n_lags > 0:
            data_columns.append("y")
        # if self.config_lagged_regressors is not None:
        #     data_columns.extend(self.config_lagged_regressors.keys())
        # if self.config_regressors is not None:
        #     data_columns.extend(self.config_regressors.keys())
        # if self.config_events is not None:
        #     data_columns.extend(self.config_events.keys())
        for column in data_columns:
            sum_na = sum(df[column].isnull())
            if sum_na > 0:
                log.warning(f"{sum_na} missing values in column {column} were detected in total. ")
                if impute_missing:
                    # use 0 substitution for holidays and events missing values
                    # if self.config_events is not None and column in self.config_events.keys():
                    #     df[column].fillna(0, inplace=True)
                    #     remaining_na = 0
                    # else:
                    df.loc[:, column], remaining_na = df_utils.fill_linear_then_rolling_avg(
                        df[column],
                        limit_linear=10,  # TODO: store in config
                        rolling=10,  # TODO: store in config
                    )
                    log.info(f"{sum_na - remaining_na} NaN values in column {column} were auto-imputed.")
                    if remaining_na > 0:
                        log.warning(
                            f"More than {2 * self.config_missing.impute_linear + self.config_missing.impute_rolling} consecutive missing values encountered in column {column}. "
                            f"{remaining_na} NA remain after auto-imputation. "
                        )
                # FIX Issue#52
                # Comment error raising to allow missing data for autoregression flow.
                # else:  # fail because set to not impute missing
                #    raise ValueError(
                #        "Missing values found. " "Please preprocess data manually or set impute_missing to True."
                #    )
                # END FIX
        if df_end_to_append is not None:
            df = pd.concat([df, df_end_to_append])
        return df

    def _handle_missing_data(self, df, freq, predicting=False):
        """Checks and normalizes new data

        Data is also auto-imputed, since impute_missing is manually set to ``True``.

        Parameters
        ----------
            df : pd.DataFrame
                dataframe containing column ``ds``, ``y``, and optionally``ID`` with all data
            freq : str
                data step sizes. Frequency of data recording,

                Note
                ----
                Any valid frequency for pd.date_range, such as ``5min``, ``D``, ``MS`` or ``auto`` (default) to automatically set frequency.
            predicting (bool): when no lags, allow NA values in ``y`` of forecast series or ``y`` to miss completely

        Returns
        -------
            pre-processed df
        """
        df, _, _, _, _ = df_utils.prep_or_copy_df(df)
        df_handled_missing = pd.DataFrame()
        for df_name, df_i in df.groupby("ID"):
            df_handled_missing_aux = self.__handle_missing_data(df_i, freq, predicting).copy(deep=True)
            df_handled_missing_aux["ID"] = df_name
            df_handled_missing = pd.concat((df_handled_missing, df_handled_missing_aux), ignore_index=True)
        return df_handled_missing
