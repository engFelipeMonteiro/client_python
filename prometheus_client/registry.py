import copy
from threading import Lock

from .metrics_core import Metric


class CollectorRegistry:
    """Metric collector registry.

    Collectors must have a no-argument method 'collect' that returns a list of
    Metric objects. The returned metrics should be consistent with the Prometheus
    exposition formats.
    """

    def __init__(self, auto_describe=False, target_info=None):
        self._collector_to_names = {}
        self._names_to_collectors = {}
        self._auto_describe = auto_describe
        self._lock = Lock()
        self._target_info = {}
        self.set_target_info(target_info)

    def register(self, collector):
        """Add a collector to the registry."""
        with self._lock:
            names = self._get_names(collector)
            duplicates = set(self._names_to_collectors).intersection(names)
            if duplicates:
                raise ValueError(
                    'Duplicated timeseries in CollectorRegistry: {}'.format(
                        duplicates))
            for name in names:
                self._names_to_collectors[name] = collector
            self._collector_to_names[collector] = names

    def unregister(self, collector):
        """Remove a collector from the registry."""
        with self._lock:
            for name in self._collector_to_names[collector]:
                del self._names_to_collectors[name]
            del self._collector_to_names[collector]

    def _get_names(self, collector):
        """Get names of timeseries the collector produces and clashes with."""
        desc_func = None
        # If there's a describe function, use it.
        try:
            desc_func = collector.describe
        except AttributeError:
            pass
        # Otherwise, if auto describe is enabled use the collect function.
        if not desc_func and self._auto_describe:
            desc_func = collector.collect

        if not desc_func:
            return []

        result = []
        type_suffixes = {
            'counter': ['_total', '_created'],
            'summary': ['_sum', '_count', '_created'],
            'histogram': ['_bucket', '_sum', '_count', '_created'],
            'gaugehistogram': ['_bucket', '_gsum', '_gcount'],
            'info': ['_info'],
        }
        for metric in desc_func():
            result.append(metric.name)
            for suffix in type_suffixes.get(metric.type, []):
                result.append(metric.name + suffix)
        return result

    def collect(self):
        """Yields metrics from the collectors in the registry."""
        collectors = None
        ti = None
        with self._lock:
            collectors = copy.copy(self._collector_to_names)
            if self._target_info:
                ti = self._target_info_metric()
        if ti:
            yield ti
        for collector in collectors:
            
            yield from collector.collect()

    def restricted_registry(self, names):
        """Returns object that only collects some metrics.

        Returns an object which upon collect() will return
        only samples with the given names.

        Intended usage is:
            generate_latest(REGISTRY.restricted_registry(['a_timeseries']))

        Experimental."""
        names = set(names)
        return RestrictedRegistry(names, self)

    def set_target_info(self, labels):
        with self._lock:
            if labels:
                if not self._target_info and 'target_info' in self._names_to_collectors:
                    raise ValueError('CollectorRegistry already contains a target_info metric')
                self._names_to_collectors['target_info'] = None
            elif self._target_info:
                self._names_to_collectors.pop('target_info', None)
            self._target_info = labels

    def get_target_info(self):
        with self._lock:
            return self._target_info

    def _target_info_metric(self):
        m = Metric('target', 'Target metadata', 'info')
        m.add_sample('target_info', self._target_info, 1)
        return m

    def get_sample_value(self, name, labels=None):
        """Returns the sample value, or None if not found.

        This is inefficient, and intended only for use in unittests.
        """
        if labels is None:
            labels = {}
        for metric in self.collect():
            for s in metric.samples:
                if s.name == name and s.labels == labels:
                    return s.value
        return None


class RestrictedRegistry:
    def __init__(self, names, registry):
        self._name_set = set(names)
        self._registry = registry

    def collect(self):
        collectors = set()
        target_info_metric = None
        with self._registry._lock:
            if 'target_info' in self._name_set and self._registry._target_info:
                target_info_metric = self._registry._target_info_metric()
            for name in self._name_set:
                if name != 'target_info' and name in self._registry._names_to_collectors:
                    collectors.add(self._registry._names_to_collectors[name])
        if target_info_metric:
            yield target_info_metric
        for collector in collectors:
            for metric in collector.collect():
                m = metric._restricted_metric(self._name_set)
                if m:
                    yield m


REGISTRY = CollectorRegistry(auto_describe=True)
