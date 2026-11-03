# coding=utf-8
"""File-based cache decorator for market data loaders.

Adapted from CodersWheel.QuickTool.file_cache.
Stores function return values as pickle files keyed by
(func_name, args, kwargs, timestamp). Cache expires via time-bucketing.

Cache location: ~/.quantnodes-research/market_cache/{YYYYMMDD}/
"""

import datetime
import hashlib
import logging
import os
import pickle
from collections import OrderedDict
from functools import wraps

logger = logging.getLogger(__name__)

# Default cache root directory
_DEFAULT_CACHE_DIR = os.path.expanduser('~/.quantnodes-research/market_cache')

format_dict = {
    'Y': '%Y',
    'm': '%Y-%m',
    'd': '%Y-%m-%d',
    'H': '%Y-%m-%d %H',
    'M': '%Y-%m-%d %H:%M',
    'S': '%Y-%m-%d %H:%M:%S',
}


def get_cache_path(enable_cache: bool = False, cache_dir: str | None = None):
    """Return the cache directory for today, creating it if needed."""
    root = cache_dir or _DEFAULT_CACHE_DIR
    dt = datetime.datetime.today().strftime('%Y%m%d')
    cache_path = os.path.join(root, dt)
    if not os.path.exists(cache_path) and enable_cache:
        os.makedirs(cache_path, exist_ok=True)
    return cache_path


def date_format(granularity: str):
    if granularity in format_dict:
        return format_dict[granularity]
    raise ValueError(f'date_format not support: {granularity}')


def prepare_args(func, arg, kwargs: dict, granularity: str = 'H',
                 exploit_func_name: bool = True, enable_cache: bool = False,
                 cache_dir: str | None = None):
    time_format_dimension = date_format(granularity)
    dt_str = datetime.datetime.now().strftime(time_format_dimension)
    kwargs = OrderedDict(sorted(kwargs.items(), key=lambda t: t[0]))
    func_name = func.__name__.__str__()
    cls_obj = func.__qualname__ != func_name
    cls_name = func.__qualname__.split('.')[0] if cls_obj else None

    if len(arg) != 0:
        arg_cls_name = arg[0].__name__ if hasattr(arg[0], '__name__') else arg[0].__class__.__name__
    else:
        arg_cls_name = None

    if cls_obj and arg_cls_name is not None and arg_cls_name == cls_name:
        arg_tuple = tuple([cls_name] + list(map(str, arg[1:])))
    else:
        arg_tuple = arg

    key = pickle.dumps([func_name, arg_tuple, kwargs, dt_str])
    if exploit_func_name:
        name = f"{func_name}_{hashlib.sha1(key).hexdigest()}_{dt_str}"
    else:
        name = hashlib.sha1(key).hexdigest()
    file_path = get_cache_path(enable_cache=enable_cache, cache_dir=cache_dir)
    return file_path, name


def write(fg, res):
    with open(fg, 'wb') as f:
        pickle.dump(res, f)


def read(fg):
    with open(fg, 'rb') as f:
        res = pickle.load(f)
    return res


def _cache(func, arg, kwargs, granularity='H', enable_cache: bool = False,
           exploit_func=True, cache_dir: str | None = None):
    if enable_cache:
        file_path, name = prepare_args(func, arg, kwargs, granularity=granularity,
                                       exploit_func_name=exploit_func,
                                       enable_cache=enable_cache, cache_dir=cache_dir)
        fg = os.path.join(file_path, name)
        if os.path.exists(fg):
            logger.debug("cache hit: %s", fg)
            return read(fg)
        else:
            res = func(*arg, **kwargs)
            write(fg, res)
            logger.debug("cache miss, wrote: %s", fg)
            return res
    else:
        return func(*arg, **kwargs)


def file_cache(**deco_arg_dict):
    """Decorator for file-based caching.

    Supports:
    - granularity: 'Y', 'm', 'd', 'H', 'M', 'S' (default 'H')
    - enable_cache: True/False to enable/disable caching
    - cache_dir: custom cache directory (default ~/.quantnodes-research/market_cache/)
    - force_refresh: passed via kwargs at call time to skip cache read
    """
    def _deco(func):
        @wraps(func)
        def __deco(*args, **kwargs):
            # Support force_refresh at call time
            force_refresh = kwargs.pop('force_refresh', False)
            if force_refresh:
                return func(*args, **kwargs)
            return _cache(func, args, kwargs, **deco_arg_dict)
        return __deco
    return _deco
