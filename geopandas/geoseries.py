from distutils.version import LooseVersion
from functools import partial
import json

import numpy as np
import pandas as pd
from pandas import Series
import pyproj
from shapely.geometry import shape, Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

from geopandas.plotting import plot_series
from geopandas.base import (
    GeoPandasBase, _delegate_property, _CoordinateIndexer)


_PYPROJ2 = LooseVersion(pyproj.__version__) >= LooseVersion('2.1.0')


def _is_empty(x):
    try:
        return x.is_empty
    except:
        return False


def _validate_geometry_data(data):
    if not all(isinstance(item, BaseGeometry) or pd.isna(item) for item in data):
        raise TypeError("Input geometry column must contain valid geometry objects.")


class GeoSeries(GeoPandasBase, Series):
    """
    A Series object designed to store shapely geometry objects.

    Parameters
    ----------
    data : array-like, dict, scalar value
        The geometries to store in the GeoSeries.
    index : array-like or Index
        The index for the GeoSeries.
    crs : str, dict (optional)
        Coordinate Reference System of the geometry objects.
    kwargs
        Additional arguments passed to the Series constructor,
         e.g. ``name``.

    Examples
    --------

    >>> from shapely.geometry import Point
    >>> s = geopandas.GeoSeries([Point(1, 1), Point(2, 2), Point(3, 3)])
    >>> s
    0    POINT (1 1)
    1    POINT (2 2)
    2    POINT (3 3)
    dtype: object

    See Also
    --------
    GeoDataFrame
    pandas.Series

    """
    _metadata = ['name', 'crs']

    def __new__(cls, data=None, index=None, crs=None, **kwargs):
        # we need to use __new__ because we need to return Series instance
        # instead of GeoSeries instance in case of non-geometry data

        if isinstance(data, BaseGeometry):
            # fix problem for scalar geometries passed, ensure the list of
            # scalars is of correct length if index is specified
            n = len(index) if index is not None else 1
            data = [data] * n

        name = kwargs.pop('name', None)

        # Use Series constructor to handle input data
        s = Series(data, index=index, name=name, **kwargs)
        # prevent trying to convert non-geometry objects
        if s.dtype != object:
            if s.empty:
                s = s.astype(object)
            else:
                return s
        # check if all geometry data, if fails return plain Series
        try:
            _validate_geometry_data(s.values)
        except TypeError:
            return s
        data = s.values
        index = s.index
        name = s.name

        self = super(GeoSeries, cls).__new__(cls)
        super(GeoSeries, self).__init__(data, index=index, name=name, **kwargs)
        self.crs = crs
        self._invalidate_sindex()
        return self

    def __init__(self, *args, **kwargs):
        # need to overwrite Series init to prevent calling it for GeoSeries
        # (doesn't know crs, all work is already done above)
        pass

    def append(self, *args, **kwargs):
        return self._wrapped_pandas_method('append', *args, **kwargs)

    @property
    def geometry(self):
        return self

    @property
    def x(self):
        """Return the x location of point geometries in a GeoSeries"""
        return _delegate_property('x', self)

    @property
    def y(self):
        """Return the y location of point geometries in a GeoSeries"""
        return _delegate_property('y', self)

    @classmethod
    def from_file(cls, filename, **kwargs):
        """Alternate constructor to create a ``GeoSeries`` from a file.

        Can load a ``GeoSeries`` from a file from any format recognized by
        `fiona`. See http://fiona.readthedocs.io/en/latest/manual.html for details.

        Parameters
        ----------
        filename : str
            File path or file handle to read from. Depending on which kwargs
            are included, the content of filename may vary. See
            http://fiona.readthedocs.io/en/latest/README.html#usage for usage details.
        kwargs : key-word arguments
            These arguments are passed to fiona.open, and can be used to
            access multi-layer data, data stored within archives (zip files),
            etc.
        """

        from geopandas import GeoDataFrame
        df = GeoDataFrame.from_file(filename, **kwargs)

        return GeoSeries(df.geometry, crs=df.crs)

    @property
    def __geo_interface__(self):
        """Returns a ``GeoSeries`` as a python feature collection.

        Implements the `geo_interface`. The returned python data structure
        represents the ``GeoSeries`` as a GeoJSON-like ``FeatureCollection``.
        Note that the features will have an empty ``properties`` dict as they
        don't have associated attributes (geometry only).
        """
        from geopandas import GeoDataFrame
        return GeoDataFrame({'geometry': self}).__geo_interface__

    def to_file(self, filename, driver="ESRI Shapefile", **kwargs):
        from geopandas import GeoDataFrame
        data = GeoDataFrame({"geometry": self,
                             "id": self.index.values},
                            index=self.index)
        data.crs = self.crs
        data.to_file(filename, driver, **kwargs)

    #
    # Implement pandas methods
    #

    @property
    def _constructor(self):
        return GeoSeries

    def _wrapped_pandas_method(self, mtd, *args, **kwargs):
        """Wrap a generic pandas method to ensure it returns a GeoSeries"""
        val = getattr(super(GeoSeries, self), mtd)(*args, **kwargs)
        if type(val) == Series:
            val.__class__ = GeoSeries
            val.crs = self.crs
            val._invalidate_sindex()
        return val

    def __getitem__(self, key):
        return self._wrapped_pandas_method('__getitem__', key)

    def sort_index(self, *args, **kwargs):
        return self._wrapped_pandas_method('sort_index', *args, **kwargs)

    def take(self, *args, **kwargs):
        return self._wrapped_pandas_method('take', *args, **kwargs)

    def select(self, *args, **kwargs):
        return self._wrapped_pandas_method('select', *args, **kwargs)

    @property
    def _can_hold_na(self):
        return False

    def __finalize__(self, other, method=None, **kwargs):
        """ propagate metadata from other to self """
        # NOTE: backported from pandas master (upcoming v0.13)
        for name in self._metadata:
            object.__setattr__(self, name, getattr(other, name, None))
        return self

    def copy(self, order='C'):
        """
        Make a copy of this GeoSeries object

        Parameters
        ----------
        deep : boolean, default True
            Make a deep copy, i.e. also copy data

        Returns
        -------
        copy : GeoSeries
        """
        # FIXME: this will likely be unnecessary in pandas >= 0.13
        return GeoSeries(self.values.copy(order), index=self.index,
                      name=self.name).__finalize__(self)

    def isna(self):
        """
        N/A values in a GeoSeries can be represented by empty geometric
        objects, in addition to standard representations such as None and
        np.nan.

        Returns
        -------
        A boolean pandas Series of the same size as the GeoSeries,
        True where a value is N/A.

        See Also
        --------
        GeoSereies.notna : inverse of isna
        """
        non_geo_null = super(GeoSeries, self).isnull()
        val = self.apply(_is_empty)
        return Series(np.logical_or(non_geo_null, val))

    def isnull(self):
        """Alias for `isna` method. See `isna` for more detail."""
        return self.isna()

    def notna(self):
        """
        N/A values in a GeoSeries can be represented by empty geometric
        objects, in addition to standard representations such as None and
        np.nan.

        Returns
        -------
        A boolean pandas Series of the same size as the GeoSeries,
        False where a value is N/A.

        See Also
        --------
        GeoSeries.isna : inverse of notna
        """
        return ~self.isna()

    def notnull(self):
        """Alias for `notna` method. See `notna` for more detail."""
        return self.notna()

    def fillna(self, value=None, method=None, inplace=False,
               **kwargs):
        """Fill NA/NaN values with a geometry (empty polygon by default).

        "method" is currently not implemented for pandas <= 0.12.
        """
        if value is None:
            value = BaseGeometry()
        return super(GeoSeries, self).fillna(value=value, method=method,
                                             inplace=inplace, **kwargs)

    def align(self, other, join='outer', level=None, copy=True,
              fill_value=None, **kwargs):
        if fill_value is None:
            fill_value = BaseGeometry()
        left, right = super(GeoSeries, self).align(other, join=join,
                                                   level=level, copy=copy,
                                                   fill_value=fill_value,
                                                   **kwargs)
        return left, right

    def __contains__(self, other):
        """Allow tests of the form "geom in s"

        Tests whether a GeoSeries contains a geometry.

        Note: This is not the same as the geometric method "contains".
        """
        if isinstance(other, BaseGeometry):
            return np.any(self.geom_equals(other))
        else:
            return False

    def plot(self, *args, **kwargs):
        """Generate a plot of the geometries in the ``GeoSeries``.

        Wraps the ``plot_series()`` function, and documentation is copied from
        there.
        """
        return plot_series(self, *args, **kwargs)

    plot.__doc__ = plot_series.__doc__

    #
    # Additional methods
    #

    def to_crs(self, crs=None, epsg=None):
        """Returns a ``GeoSeries`` with all geometries transformed to a new
        coordinate reference system.

        Transform all geometries in a GeoSeries to a different coordinate
        reference system.  The ``crs`` attribute on the current GeoSeries must
        be set.  Either ``crs`` in string or dictionary form or an EPSG code
        may be specified for output.

        This method will transform all points in all objects.  It has no notion
        or projecting entire geometries.  All segments joining points are
        assumed to be lines in the current projection, not geodesics.  Objects
        crossing the dateline (or other projection boundary) will have
        undesirable behavior.

        Parameters
        ----------
        crs : dict or str
            Output projection parameters as string or in dictionary form.
        epsg : int
            EPSG code specifying output projection.
        """
        from fiona.crs import from_epsg
        if self.crs is None:
            raise ValueError('Cannot transform naive geometries.  '
                             'Please set a crs on the object first.')
        if crs is None:
            try:
                crs = from_epsg(epsg)
            except TypeError:
                raise TypeError('Must set either crs or epsg for output.')
        proj_in = pyproj.Proj(self.crs, preserve_units=True)
        proj_out = pyproj.Proj(crs, preserve_units=True)
        if _PYPROJ2:
            transformer = pyproj.Transformer.from_proj(proj_in, proj_out)
            project = transformer.transform
        else:
            project = partial(pyproj.transform, proj_in, proj_out)
        result = self.apply(lambda geom: transform(project, geom))
        result.__class__ = GeoSeries
        result.crs = crs
        result._invalidate_sindex()
        return result

    def to_json(self, **kwargs):
        """
        Returns a GeoJSON string representation of the GeoSeries.

        Parameters
        ----------
        *kwargs* that will be passed to json.dumps().
        """
        return json.dumps(self.__geo_interface__, **kwargs)

    #
    # Implement standard operators for GeoSeries
    #

    def __xor__(self, other):
        """Implement ^ operator as for builtin set type"""
        return self.symmetric_difference(other)

    def __or__(self, other):
        """Implement | operator as for builtin set type"""
        return self.union(other)

    def __and__(self, other):
        """Implement & operator as for builtin set type"""
        return self.intersection(other)

    def __sub__(self, other):
        """Implement - operator as for builtin set type"""
        return self.difference(other)


GeoSeries._create_indexer('cx', _CoordinateIndexer)
