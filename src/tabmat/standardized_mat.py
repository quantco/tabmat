from typing import Optional, Union

import numpy as np
from scipy import sparse as sps

from .dense_matrix import DenseMatrix
from .matrix_base import MatrixBase
from .sparse_matrix import SparseMatrix
from .util import (
    check_matvec_dimensions,
    check_sandwich_compatible,
    check_transpose_matvec_out_shape,
    set_up_rows_or_cols,
    setup_restrictions,
)


class StandardizedMatrix:
    """
    StandardizedMatrix allows for storing a matrix standardized to have columns
    that have mean zero and standard deviation one without modifying underlying
    sparse matrices.

    To be precise, for a StandardizedMatrix:

    ::

        self[i, j] = (self.mult[j] * self.mat[i, j]) + self.shift[j]

    This class is returned from
    :meth:`MatrixBase.standardize <tabmat.MatrixBase.standardize>`.
    """

    __array_priority__ = 11

    def __init__(
        self,
        mat: MatrixBase,
        shift: Union[np.ndarray, list],
        mult: Optional[Union[np.ndarray, list]] = None,
    ):
        shift_arr = np.atleast_1d(np.squeeze(shift))
        expected_shape = (mat.shape[1],)
        if not isinstance(mat, MatrixBase):
            raise TypeError("mat should be an instance of a MatrixBase subclass.")
        if not shift_arr.shape == expected_shape:
            raise ValueError(
                f"""Expected shift to be able to conform to shape {expected_shape},
            but it has shape {np.asarray(shift).shape}"""
            )

        if mult is not None:
            mult_arr = np.atleast_1d(np.squeeze(mult))
            if not mult_arr.shape == expected_shape:
                raise ValueError(
                    f"""Expected mult to be able to conform to shape {expected_shape},
                but it has shape {np.asarray(mult).shape}"""
                )
        else:
            mult_arr = None

        self.shift = shift_arr
        self.mult = mult_arr
        self.mat = mat
        self.shape = mat.shape
        self.ndim = mat.ndim
        self.dtype = mat.dtype

    def matvec(
        self,
        other_mat: Union[np.ndarray, list],
        cols: Optional[np.ndarray] = None,
        out: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """
        Perform self[:, cols] @ other[cols].

        This function returns a dense output, so it is best geared for the
        matrix-vector case.
        """
        cols = set_up_rows_or_cols(cols, self.shape[1])

        other_mat = np.asarray(other_mat)
        check_matvec_dimensions(self, other_mat, transpose=False)
        mult_other = other_mat
        if self.mult is not None:
            mult = self.mult
            # Avoiding an outer product by matching dimensions.
            for _ in range(len(other_mat.shape) - 1):
                mult = mult[:, np.newaxis]
            mult_other = mult * other_mat
        mat_part = self.mat.matvec(mult_other, cols, out=out)

        # Add shift part to mat_part
        shift_part = self.shift[cols].dot(other_mat[cols, ...])  # scalar
        mat_part += shift_part
        return mat_part

    def getcol(self, i: int):
        """
        Return matrix column at specified index.

        Returns a StandardizedMatrix.

        >>> from scipy import sparse as sps
        >>> x = StandardizedMatrix(SparseMatrix(sps.eye(3).tocsc()), shift=[0, 1, -2])
        >>> col_1 = x.getcol(1)
        >>> isinstance(col_1, StandardizedMatrix)
        True
        >>> col_1.toarray()
        array([[1.],
               [2.],
               [1.]])
        """
        mult = None
        if self.mult is not None:
            mult = [self.mult[i]]
        col = self.mat.getcol(i)
        if isinstance(col, sps.csc_matrix) and not isinstance(col, MatrixBase):
            col = SparseMatrix(col)
        return StandardizedMatrix(col, [self.shift[i]], mult)

    def sandwich(
        self,
        d: np.ndarray,
        rows: Optional[np.ndarray] = None,
        cols: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """Perform a sandwich product: X.T @ diag(d) @ X."""
        if not hasattr(d, "dtype"):
            d = np.asarray(d)
        check_sandwich_compatible(self, d)
        # stat_mat = mat * mult[newaxis, :] + shift[newaxis, :]
        # stat_mat.T @ d[:, newaxis] * stat_mat
        # = mult[:, newaxis] * mat.T @ d[:, newaxis] * mat * mult[newaxis, :] +   (1)
        #   mult[:, newaxis] * mat.T @ d[:, newaxis] * np.outer(ones, shift) +    (2)
        #   shift[:, newaxis] @ d[:, newaxis] * mat * mult[newaxis, :] +          (3)
        #   shift[:, newaxis] @ d[:, newaxis] * shift[newaxis, :]                 (4)
        #
        # (1) = self.mat.sandwich(d) * np.outer(limited_mult, limited_mult)
        # (2) = mult * self.transpose_matvec(d) * shift[newaxis, :]
        if rows is not None or cols is not None:
            setup_rows, setup_cols = setup_restrictions(self.shape, rows, cols)
            if rows is not None:
                rows = setup_rows
            if cols is not None:
                cols = setup_cols

        term1 = self.mat.sandwich(d, rows, cols)
        d_mat = self.mat.transpose_matvec(d, rows, cols)
        if self.mult is not None:
            limited_mult = self.mult[cols] if cols is not None else self.mult
            d_mat *= limited_mult
        term2 = np.outer(d_mat, self.shift[cols])

        limited_shift = self.shift[cols] if cols is not None else self.shift
        limited_d = d[rows] if rows is not None else d
        term3 = np.outer(limited_shift, d_mat)
        term4 = np.outer(limited_shift, limited_shift) * np.sum(limited_d)
        res = term2 + term3 + term4
        if isinstance(term1, sps.dia_matrix):
            idx = np.arange(res.shape[0])
            to_add = term1.data[0, :]
            if self.mult is not None:
                to_add *= limited_mult**2
            res[idx, idx] += to_add
        else:
            to_add = term1
            if self.mult is not None:
                to_add *= np.outer(limited_mult, limited_mult)
            res += to_add
        return res

    def unstandardize(self) -> MatrixBase:
        """Get unstandardized (base) matrix."""
        return self.mat

    def transpose_matvec(
        self,
        other: Union[np.ndarray, list],
        rows: Optional[np.ndarray] = None,
        cols: Optional[np.ndarray] = None,
        out: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """
        Perform: self[rows, cols].T @ vec[rows].

        Let self.shape = (N, K) and other.shape = (M, N).
        Let shift_mat = outer(ones(N), shift)

        (X.T @ other)[k, i] = (X.mat.T @ other)[k, i] + (shift_mat @ other)[k, i]
        (shift_mat @ other)[k, i] = (outer(shift, ones(N)) @ other)[k, i]
        = sum_j outer(shift, ones(N))[k, j] other[j, i]
        = sum_j shift[k] other[j, i]
        = shift[k] other.sum(0)[i]
        = outer(shift, other.sum(0))[k, i]

        With row and col restrictions:

        self.transpose_matvec(other, rows, cols)[i, j]
            = self.mat.transpose_matvec(other, rows, cols)[i, j]
              + (outer(self.shift, ones(N))[rows, cols] @ other[cols])
            = self.mat.transpose_matvec(other, rows, cols)[i, j]
              + shift[cols[i]] other.sum(0)[rows[j]
        """
        check_transpose_matvec_out_shape(self, out)
        other = np.asarray(other)
        check_matvec_dimensions(self, other, transpose=True)
        res = self.mat.transpose_matvec(other, rows, cols)

        rows, cols = setup_restrictions(self.shape, rows, cols)
        other_sum = np.sum(other[rows], 0)

        shift_part_tmp = np.outer(self.shift[cols], other_sum)
        output_shape = ((self.shape[1] if cols is None else len(cols)),) + res.shape[1:]
        shift_part = np.reshape(shift_part_tmp, output_shape)

        if self.mult is not None:
            mult = self.mult
            # Avoiding an outer product by matching dimensions.
            for _ in range(res.ndim - 1):
                mult = mult[:, np.newaxis]
            res *= mult[cols]
        res += shift_part

        if out is None:
            return res
        else:
            out[cols] += res
            return out

    def __rmatmul__(self, other: Union[np.ndarray, list]) -> np.ndarray:
        """
        Return matrix multiplication with other.

        other @ X = (X.T @ other.T).T = X.transpose_matvec(other.T).T

        Parameters
        ----------
        other: array-like

        Returns
        -------
        array

        """
        if not hasattr(other, "T"):
            other = np.asarray(other)
        return self.transpose_matvec(other.T).T  # type: ignore

    def __matmul__(self, other):
        """Define the behavior of 'self @ other'."""
        return self.matvec(other)

    def multiply(self, other) -> DenseMatrix:
        """Element-wise multiplication.

        Note that the output of this function is always a DenseMatrix and might
        require a lot more memory. This assumes that ``other`` is a vector of
        size ``self.shape[0]``.
        """
        return DenseMatrix(self.toarray()).multiply(other)

    def toarray(self) -> np.ndarray:
        """Return array representation of matrix."""
        mat_part = self.mat.toarray()
        if self.mult is not None:
            mat_part = self.mult[None, :] * mat_part
        return mat_part + self.shift[None, :]

    @property
    def A(self) -> np.ndarray:
        """Return array representation of self."""
        return self.toarray()

    def astype(self, dtype, order="K", casting="unsafe", copy=True):
        """Return StandardizedMatrix cast to new type."""
        return type(self)(
            self.mat.astype(dtype, casting=casting, copy=copy),
            self.shift.astype(dtype, order=order, casting=casting, copy=copy),
        )

    def __getitem__(self, item):
        if isinstance(item, tuple):
            row, col = item
        else:
            row = item
            col = slice(None, None, None)

        mat_part = self.mat.__getitem__(item)
        shift_part = self.shift[col]
        mult_part = self.mult
        if mult_part is not None:
            mult_part = np.atleast_1d(mult_part[col])

        if isinstance(row, int):
            out = mat_part.toarray()
            if mult_part is not None:
                out = out * mult_part
            return out + shift_part

        return StandardizedMatrix(mat_part, np.atleast_1d(shift_part), mult_part)

    def __repr__(self):
        out = f"""StandardizedMat. Mat: {type(self.mat)} of shape {self.mat.shape}.
        Shift: {self.shift}
        Mult: {self.mult}
        """
        return out

    def get_names(
        self,
        type: str = "column",
        missing_prefix: Optional[str] = None,
        indices: Optional[list[int]] = None,
    ) -> list[Optional[str]]:
        """Get column names.

        For columns that do not have a name, a default name is created using the
        following pattern: ``"{missing_prefix}{start_index + i}"`` where ``i`` is
        the index of the column.

        Parameters
        ----------
        type: str {'column'|'term'}
            Whether to get column names or term names. The main difference is
            that a categorical submatrix counts as one term, but can count as
            multiple columns. Furthermore, matrices created from formulas
            distinguish between columns and terms (c.f. ``formulaic`` docs).
        missing_prefix: Optional[str], default None
            Prefix to use for columns that do not have a name. If None, then no
            default name is created.
        indices
            The indices used for columns that do not have a name. If ``None``,
            then the indices are ``list(range(self.shape[1]))``.

        Returns
        -------
        list[Optional[str]]
            Column names.
        """
        return self.mat.get_names(type, missing_prefix, indices)

    def set_names(self, names: Union[str, list[Optional[str]]], type: str = "column"):
        """Set column names.

        Parameters
        ----------
        names: list[Optional[str]]
            Names to set.
        type: str {'column'|'term'}
            Whether to get column names or term names. The main difference is
            that a categorical submatrix counts as one term, but can count as
            multiple columns. Furthermore, matrices created from formulas
            distinguish between columns and terms (c.f. ``formulaic`` docs).
        """
        self.mat.set_names(names, type)

    @property
    def column_names(self):
        """Column names of the matrix."""
        return self.get_names(type="column")

    @column_names.setter
    def column_names(self, names: list[Optional[str]]):
        self.set_names(names, type="column")

    @property
    def term_names(self):
        """Term names of the matrix.

        For differences between column names and term names, see ``get_names``.
        """
        return self.get_names(type="term")

    @term_names.setter
    def term_names(self, names: list[Optional[str]]):
        self.set_names(names, type="term")
