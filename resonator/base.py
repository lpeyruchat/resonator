"""
This module contains the ResonatorFitter class and the MeasurementModelResonance object.
"""
from __future__ import absolute_import, division, print_function
from collections import namedtuple

import lmfit
import numpy as np
from scipy.constants import h, pi


# A container for both measured and fit data that is used by the see.py plotting module.
# The data arrays may either all have the background divided out or not.
# The measurement_ arrays are the measurement frequency and corresponding response data;
# The model_ arrays are the model frequency and response data evaluated there;
# The resonance_ points are the resonance frequency (float) and the model (complex) evaluated there.
MeasurementModelResonance = namedtuple('MeasurementModelResonance',
                                       field_names=['measurement_frequency', 'measurement_data',
                                                    'model_frequency', 'model_data',
                                                    'resonance_frequency', 'resonance_data'])


class ResonatorModel(lmfit.model.Model):
    reference_point = None

    io_coupling_coefficient = None

    def guess(self, data, frequency, **kwds):
        """Subclasses should implement their own guessing function that returns reasonable initial fit values."""
        return self.make_params()


class BackgroundModel(lmfit.model.Model):

    def guess(self, data, frequency, **kwds):
        """Subclasses should implement their own guessing function that returns reasonable initial fit values."""
        return self.make_params()


class ResonatorFitter(object):
    """
    This class is a wrapper for composite models that represent the response of a resonator multiplied by the background
    response of the system. Its subclasses represent models for resonators used in specific configurations.
    """

    def __init__(self, frequency, data, foreground_model, background_model, errors=None, params=None, **fit_kwds):
        """
        Fit the given data using the given resonator model.

        :param frequency: an array of floats containing the frequencies at which the data was measured.
        :param data: an array of complex numbers containing the data, probably forward transmission S_{21} or forward
          reflection S_{11}.
        :param foreground_model: an instance (not the class) of a ResonatorModel subclass representing the resonator
          to be fit.
        :param background_model: an instance (not the class) of a background.BackgroundModel subclass representing the
          background, meaning everything other than the target resonator, including effects such as gain, cable delay,
          and other resonances.
        :param errors: Standard error of the mean for the real and imaginary parts of the data, used to assign weights
          in the least-squares fit; the default of None means to use equal errors and thus equal weights for each point;
          to exclude a point, set the errors to (1 + 1j) * np.inf for that point.
        :param params: a lmfit.parameter.Parameters object containing Parameters to use as initial values for the fit;
        these are passed to fit() and will overwrite Parameters with the same names obtained from guess().
        :param fit_kwds: keyword arguments passed directly to lmfit.model.Model.fit(), except for params, as explained
          above; see the lmfit documentation.
        """
        if not np.iscomplexobj(data):
            raise TypeError("Resonator data must be complex.")
        if errors is not None and not np.iscomplexobj(errors):
            raise TypeError("Resonator errors must be complex.")
        self.frequency = frequency
        self.data = data
        self.errors = errors
        self.model = background_model * foreground_model  # lmfit.model.CompositeModel
        self.result = None  # This is updated immediately by the next line
        self.fit(params=params, **fit_kwds)

    def __getattr__(self, attr):
        if attr.endswith('_error'):
            name = attr[:-len('_error')]
            try:
                return self.result.params[name].stderr
            except KeyError:
                raise AttributeError("Couldn't find error for {} in self.result".format(name))
        else:
            try:
                return self.result.params[attr].value
            except KeyError:
                raise AttributeError("'{}' object has no attribute '{}'".format(self.__class__.__name__, attr))

    def __dir__(self):
        return sorted(set(dir(ResonatorFitter) +
                          list(self.__dict__.keys()) +
                          list(self.result.params.keys()) +
                          [name + '_error' for name in self.result.params.keys()]))

    def __str__(self):
        return "{}: {} * {}".format(self.__class__.__name__, self.background_model.__class__.__name__,
                                    self.foreground_model.__class__.__name__)

    @property
    def weights(self):
        """
        The weights, calculated from self.errors, that are used to weight the residuals.
        See https://github.com/numpy/numpy/issues/5261
        """
        if self.errors is None:
            return None
        else:
            return 1 / self.errors.real + 1j / self.errors.imag

    @property
    def background_model(self):
        """The lmfit.model.Model object representing the background."""
        return self.model.left

    @property
    def foreground_model(self):
        """The lmfit.model.Model object representing the foreground."""
        return self.model.right

    def guess(self, frequency, data):
        """
        Return a lmfit.parameter.Parameters object containing reasonable intial values for all of the fit parameters.

        First, the background model `guess` method is called with the frequency array and the data array (divided by
        the foreground reference point). This should return a Parameters object containing reasonable initial values
        for the background model. The foreground model `guess` method is then called with the frequency array and the
        data array divided by the background model evaluated using the guessed parameters. The parameters may have
        lower or upper bounds that are set by the individual `guess` methods of the background and foreground models.
        The individual parameters produced by this function can be overridden using the params keyword argument of
        the `__init__` or `__fit__` methods, which allows the user to provide initial values for some or all of the
        parameters and to override the default bounds.

        :param frequency: an array of floats that are the frequencies corresponding to the data.
        :param data: an array of complex scattering parameter values.
        :return: lmfit.parameter.Parameters
        """
        # The signature of lmfit.model.Model.guess is guess(data, **kwds)
        guess = self.background_model.guess(data=data / self.foreground_model.reference_point, frequency=frequency)
        background_guess = self.background_model.eval(params=guess, frequency=frequency)
        guess.update(self.foreground_model.guess(data=data / background_guess, frequency=frequency))
        return guess

    def fit(self, params=None, **fit_kwds):
        """
        Fit the object's model to its data, overwriting the existing result.

        :param params: a lmfit.parameter.Parameters object containing Parameters that will overwrite the parameters obtained from
          self.guess(), which uses the guessing functions of first the background and then the foreground.
        :param fit_kwds: a dict of keywords passed directly to lmfit.model.Model.fit().
        :return: None
        """
        initial_params = self.guess(frequency=self.frequency, data=self.data)
        if params is not None:
            initial_params.update(params)
        self.result = self.model.fit(frequency=self.frequency, data=self.data, weights=self.weights,
                                     params=initial_params, **fit_kwds)

    def model_values(self, frequency=None, params=None):
        """
        Return the model (background * foreground) evaluated at the given frequencies with the given parameters.

        :param frequency: float or array of floats; the default is to use the frequencies corresponding to the data.
        :param params: lmfit.parameter.Parameters object; the default is to use the current best-fit parameters.
        :return: array[complex]
        """
        if params is None:
            params = self.result.params
        if frequency is None:
            frequency = self.frequency
        return self.model.eval(frequency=frequency, params=params)

    def initial_model_values(self, frequency=None):
        """
        Return the model (background * foreground) evaluated at the given frequencies with the initial parameters.

        :param frequency: float or array of floats; the default is to use the frequencies corresponding to the data.
        :return: array[complex]
        """
        if frequency is None:
            frequency = self.frequency
        return self.model.eval(params=self.result.initial_params, frequency=frequency)

    def remove_background(self, frequency, data):
        """
        Return scattering data normalied to the foreground (resonator) plane, calculated by dividing the data by the
        background evaluated at the given frequency or frequencies using the current best-fit params.

        When used to normalize data taken at an array of frequencies, as is used to fit a resonance, this should produce
        a circle when plotted in the complex plane. This is a good check that the background model is correct and that
        the fit is good. (For a resonator approaching or above the bifurcation point the points should still lie on a
        circle, but the points will be shifted so part of the circle may be missing.)

        When used to normalize continuous-wave data taken at a single frequency, the result should be a cloud of points
        that lie on the normalized resonance circle.

        If this method is called with The returned data should produce a circle somewhere in the complex plane. For nonlinear resonators, part of the
        circle may be missing.

        :param frequency: float or array of floats representing frequencies corresponding to the given data.
        :param data: complex or array of complex scattering data to be normalized.
        :return: array[complex]
        """
        return data / self.background_model.eval(params=self.result.params, frequency=frequency)

    @property
    def foreground_data(self):
        """The measured data divided by the background model calculated at the same frequencies."""
        return self.remove_background(frequency=self.frequency, data=self.data)

    # ToDo: replace with individual methods
    def measurement_model_resonance(self, normalize=False, num_model_points=None):
        """
        Return a MeasurementModelResonance object (see above) containing three pairs of frequency and data values:
        - arrays containing the measurement frequencies and measured data;
        - arrays containing various frequencies within the span of the measurement frequencies and the model evaluated
          at these frequencies;
        - the model resonance frequency and the model evaluated at this frequency.

        :param normalize: If True, return all data values with the background model removed.
        :param num_model_points: The number of frequencies to use in evaluating the model between the minimum and
          maximum measurement frequencies; if None (default), evaluate the model at the measurement frequencies.
        :return: MeasurementModelResonance containing frequency and data arrays.
        """
        measurement_frequency = self.frequency.copy()
        measurement_data = self.data.copy()
        if num_model_points is None:
            model_frequency = self.frequency.copy()
        else:
            model_frequency = np.linspace(measurement_frequency.min(), measurement_frequency.max(), num_model_points)
        model_data = self.model.eval(params=self.result.params, frequency=model_frequency)
        resonance_data = self.model.eval(params=self.result.params, frequency=self.resonance_frequency)
        if normalize:
            measurement_data = self.remove_background(frequency=measurement_frequency, data=measurement_data)
            model_data = self.remove_background(frequency=model_frequency, data=model_data)
            resonance_data = self.remove_background(frequency=self.resonance_frequency, data=resonance_data)
        return MeasurementModelResonance(measurement_frequency, measurement_data,
                                         model_frequency, model_data,
                                         self.resonance_frequency, resonance_data)

    def invert(self, scattering_data):
        """
        Return the resonator detuning and internal_loss that correspond to the given data, obtained by inverting the
        resonator model.

        Many parameters of superconducting microresonators are constant under different measurement conditions. For
        example, the coupling loss is typically defined lithographically. However, the resonance frequency (and thus
        the detuning from the measurement frequency) and the internal dissipation can vary due to various physical
        effects, such as a changing magnetic field or a changing density of quasiparticles. When using a resonator as
        a transducer, these detuning and dissipation signals are the desired quantities.

        This calculation assumes that only the detuning and internal loss vary in time. In this case, when measuring at
        constant frequency (i.e. in continuous-wave mode with a VNA), each measured complex number in the scattering
        parameter complex plane (S21 or S11) corresponds to a point in the complex plane defined by
          z = internal_loss + 2j * detuning.
        This quantity appears in all of the resonator models used in this package. In order for this analytic inversion
        of the scattering data to be strictly accurate, the data bandwidth must be less than the single-sided
        resonator bandwidth given by
          f_ss = f_r * (coupling_loss + internal_loss) / 2.
        If this is not the case, a more complicated calculation involving the resonator transfer function may be
        required. See J. Zmuidzinas, Annu. Rev. Cond. Matter Phys. 3, 169 (2012), available at
        https://doi.org/10.1146/annurev-conmatphys-020911-125022

        The scattering data must be normalized to the foreground (resonator) plane. That is, for a shunt-coupled
        resonator the data should equal 1 + 0j far from resonance; for a reflection from a resonator the data should
        equal -1 + 0j far from resonance; and for the transmission configuration, the data should equal
        1 / (1 + internal_loss / coupling_loss) + 0j exactly at the resonance. Raw data taken in the same configuration
        as the data used for the fit can be analyzed using remove_background_and_invert().

        :param scattering_data: Normalized scattering data, typically time-ordered.
        :return: detuning, internal_loss; both array[float], calculated by inverting the the resonator model.
        """
        raise NotImplementedError("Subclasses should implement this using their scattering parameter model.")


    def remove_background_and_invert(self, raw_scattering_data, measurement_frequency):
        """
        Return the resonator detuning and internal_loss that correspond to the given data, obtained by inverting the
        resonator model. The given data array is normalized to the resonator plane by dividing it by the single complex
        background value at the given measurement frequency, and the resulting values are passed to invert().

        :param raw_scattering_data: Raw scattering data, typically time-ordered.
        :param measurement_frequency: the frequency at which the scattering data was measured.
        :return: detuning, internal_loss; see invert().
        """
        return self.invert(self.remove_background(frequency=measurement_frequency, data=raw_scattering_data))

    # Aliases for common resonator properties

    @property
    def f_r(self):
        """Alias for resonance_frequency."""
        return self.resonance_frequency

    @property
    def f_r_error(self):
        """Alias for resonance_frequency_error."""
        return self.resonance_frequency_error

    @property
    def omega_r(self):
        """The resonance angular frequency."""
        return 2 * pi * self.resonance_frequency

    @property
    def omega_r_error(self):
        """The standard error of the resonance angular frequency."""
        return 2 * pi * self.resonance_frequency_error

    @property
    def total_loss(self):
        """
        The total loss is the sum of the coupling and internal losses, which is inverse of the total (or loaded or
        resonator) quality factor.
        """
        return self.internal_loss + self.coupling_loss

    @property
    def total_loss_error(self):
        """Assume that the errors of the internal loss and coupling loss are independent."""
        return (self.internal_loss_error ** 2 + self.coupling_loss_error ** 2) ** (1 / 2)

    @property
    def coupling_quality_factor(self):
        """The coupling quality factor."""
        return 1 / self.coupling_loss

    @property
    def Q_c(self):
        """The coupling quality factor."""
        return self.coupling_quality_factor

    @property
    def coupling_quality_factor_error(self):
        """The standard error of the coupling quality factor."""
        return self.coupling_loss_error / self.coupling_loss ** 2

    @property
    def Q_c_error(self):
        """The standard error of the coupling quality factor."""
        return self.coupling_quality_factor_error

    @property
    def internal_quality_factor(self):
        """The internal quality factor."""
        return 1 / self.internal_loss

    @property
    def Q_i(self):
        """The internal quality factor."""
        return self.internal_quality_factor

    @property
    def internal_quality_factor_error(self):
        """The standard error of the internal quality factor."""
        return self.internal_loss_error / self.internal_loss ** 2

    @property
    def Q_i_error(self):
        """The standard error of the internal quality factor."""
        return self.internal_quality_factor_error

    @property
    def total_quality_factor(self):
        """The total (or resonator, or loaded) quality factor."""
        return 1 / (self.internal_loss + self.coupling_loss)

    @property
    def Q_t(self):
        """The total (or resonator, or loaded) quality factor."""
        return self.total_quality_factor

    @property
    def total_quality_factor_error(self):
        """The standard error of the total (or resonator, or loaded) quality factor."""
        return self.total_loss_error / self.total_loss ** 2

    @property
    def Q_t_error(self):
        """The standard error of the total (or resonator, or loaded) quality factor."""
        return self.total_quality_factor_error

    @property
    def coupling_energy_decay_rate(self):
        """The energy decay rate through the coupling to the output port."""
        return self.omega_r * self.coupling_loss

    @property
    def coupling_energy_decay_rate_error(self):
        """
        The standard error of the coupling energy decay rate, calculated by assuming that the errors of the resonance
        frequency and coupling loss are independent.
        """
        return self.coupling_energy_decay_rate * ((self.resonance_frequency_error / self.resonance_frequency) ** 2
                                                  + (self.coupling_loss_error / self.coupling_loss) ** 2) ** (1 / 2)

    @property
    def internal_energy_decay_rate(self):
        """The energy decay rate due to all channels other than the output port."""
        return self.omega_r * self.internal_loss

    @property
    def internal_energy_decay_rate_error(self):
        """
        The standard error of the coupling energy decay rate, calculated by assuming that the errors of the resonance
        frequency and internal loss are independent.
        """
        return self.internal_energy_decay_rate * ((self.resonance_frequency_error / self.resonance_frequency) ** 2 +
                                                  (self.internal_loss_error / self.internal_loss) ** 2) ** (1 / 2)

    @property
    def total_energy_decay_rate(self):
        """The total (coupling plus internal) energy loss rate."""
        return self.omega_r * (self.internal_loss + self.coupling_loss)

    @property
    def total_energy_decay_rate_error(self):
        """
        The total energy decay rate, calculated by assuming that the errors of the resonance frequency, internal loss,
        and coupling loss are independent.
        """
        return self.total_energy_decay_rate * ((self.resonance_frequency_error / self.resonance_frequency) ** 2 +
                                               (self.total_loss_error / self.total_loss) ** 2) ** (1 / 2)

    def photon_number(self, input_frequency, input_rate):
        """
        Return the average photon number in the resonator calculated using the fit parameters, assuming an input signal
        at the given input frequency and input rate.

        :param input_frequency: float or array[float]; the frequency of the input signal, in Hz.
        :param input_rate: the input photon rate, in photons per second.
        :return: the photon number.
        """
        return self.foreground_model.photon_number(frequency=input_frequency,
                                                   resonance_frequency=self.resonance_frequency,
                                                   coupling_loss=self.coupling_loss, internal_loss=self.internal_loss,
                                                   input_rate=input_rate)

    def photon_number_from_power(self, input_frequency, input_power_dBm):
        """
        Return the average photon number in the resonator calculated using the fit parameters, assuming an input signal
        at the given input frequency and input rate.

        :param input_frequency: float or array[float]; the frequency of the input signal, in Hz.
        :param input_power_dBm: the input power, in dBm.
        :return: the photon number.
        """
        return self.photon_number(input_frequency=input_frequency,
                                  input_rate=1e-3 * 10 ** (input_power_dBm / 10) / (h * input_frequency))
