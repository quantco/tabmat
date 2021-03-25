from typing import List, Optional, Union
import platform

import numpy as np
from scipy import sparse as sps

from .ext.sparse import (
    csc_rmatvec,
    csr_dense_sandwich,
    csr_matvec,
    sparse_sandwich,
    transpose_square_dot_weights,
)
from .matrix_base import MatrixBase
from .util import (
    check_matvec_out_shape,
    check_transpose_matvec_out_shape,
    set_up_rows_or_cols,
    setup_restrictions,
)

if platform.machine() in {"arm64", "aarch64"}:
    def dot_product_mkl(matrix_a, matrix_b, out):
        return np.dot(matrix_a, matrix_b, out=out)
else:
    # Intel MKL obviously is only available on Intel-based architectures
    from sparse_dot_mkl import dot_product_mkl

class SparseMatrix(sps.csc_matrix, MatrixBase):
    """
    A scipy.sparse csc matrix subclass that will use MKL for sparse
    matrix-vector products and will use the fast sparse_sandwich function
    for sandwich products.
    """

    def __init__(self, arg1, shape=None, dtype=None, copy=False):
        """
        Instantiate in the same way as scipy.sparse.csc_matrix
        """
        super().__init__(arg1, shape, dtype, copy)
        self.idx_dtype = max(self.indices.dtype, self.indptr.dtype)
        if self.indices.dtype != self.idx_dtype:
            self.indices = self.indices.astype(self.idx_dtype)
        if self.indptr.dtype != self.idx_dtype:
            self.indptr = self.indptr.astype(self.idx_dtype)
        assert self.indices.dtype == self.idx_dtype

        if not self.has_sorted_indices:
            self.sort_indices()
        self._x_csr = None

    @property
    def x_csr(self):
        if self._x_csr is None:
            self._x_csr = self.tocsr(copy=False)
            if self._x_csr.indices.dtype != self.idx_dtype:
                self._x_csr.indices = self._x_csr.indices.astype(self.idx_dtype)
            if self._x_csr.indptr.dtype != self.idx_dtype:
                self._x_csr.indptr = self._x_csr.indptr.astype(self.idx_dtype)

        return self._x_csr

    def sandwich(
        self, d: np.ndarray, rows: np.ndarray = None, cols: np.ndarray = None
    ) -> np.ndarray:
        if not hasattr(d, "dtype"):
            d = np.asarray(d)
        if not self.dtype == d.dtype:
            raise TypeError(
                f"""self and d need to be of same dtype, either np.float64
                or np.float32. self is of type {self.dtype}, while d is of type
                {d.dtype}."""
            )

        rows, cols = setup_restrictions(self.shape, rows, cols, dtype=self.idx_dtype)
        return sparse_sandwich(self, self.x_csr, d, rows, cols)

    def cross_sandwich(
        self,
        other: MatrixBase,
        d: np.ndarray,
        rows: np.ndarray,
        L_cols: Optional[np.ndarray] = None,
        R_cols: Optional[np.ndarray] = None,
    ):
        if isinstance(other, np.ndarray):
            return self.sandwich_dense(other, d, rows, L_cols, R_cols)
        from .categorical_matrix import CategoricalMatrix

        if isinstance(other, CategoricalMatrix):
            return other.cross_sandwich(self, d, rows, R_cols, L_cols).T
        raise TypeError

    def sandwich_dense(
        self,
        B: np.ndarray,
        d: np.ndarray,
        rows: np.ndarray,
        L_cols: np.ndarray,
        R_cols: np.ndarray,
    ) -> np.ndarray:
        """
        sandwich product: self.T @ diag(d) @ B
        """
        if not hasattr(d, "dtype"):
            d = np.asarray(d)

        if self.dtype != d.dtype or B.dtype != d.dtype:
            raise TypeError(
                f"""self, B and d all need to be of same dtype, either
                np.float64 or np.float32. This matrix is of type {self.dtype},
                B is of type {B.dtype}, while d is of type {d.dtype}."""
            )
        if np.issubdtype(d.dtype, np.signedinteger):
            d = d.astype(float)

        rows, L_cols = setup_restrictions(self.shape, rows, L_cols)
        R_cols = set_up_rows_or_cols(R_cols, B.shape[1])
        return csr_dense_sandwich(self.x_csr, B, d, rows, L_cols, R_cols)

    def matvec_helper(
        self,
        vec: Union[List, np.ndarray],
        rows: Optional[np.ndarray],
        cols: Optional[np.ndarray],
        out: Optional[np.ndarray],
        transpose: bool,
    ):
        X = self.T if transpose else self
        matrix_matvec = lambda x, v: sps.csc_matrix.dot(x, v)
        if transpose:
            matrix_matvec = lambda x, v: sps.csr_matrix.dot(x.T, v)

        vec = np.asarray(vec)

        # NOTE: We assume that rows and cols are unique
        unrestricted_rows = rows is None or len(rows) == self.shape[0]
        unrestricted_cols = cols is None or len(cols) == self.shape[1]
        if unrestricted_rows and unrestricted_cols:
            if vec.ndim == 1:
                return dot_product_mkl(X, vec, out=out)
            elif vec.ndim == 2 and vec.shape[1] == 1:
                out_arr = None if out is None else out[:, 0]
                return dot_product_mkl(X, vec[:, 0], out=out_arr)[:, None]
            res = matrix_matvec(self, vec)
            if out is None:
                return res
            out += res
            return out
        else:
            rows, cols = setup_restrictions(
                self.shape, rows, cols, dtype=self.idx_dtype
            )
            if transpose:
                fast_fnc = lambda v: csc_rmatvec(self, v, rows, cols)
            else:
                fast_fnc = lambda v: csr_matvec(self.x_csr, v, rows, cols)
            if vec.ndim == 1:
                res = fast_fnc(vec)
            elif vec.ndim == 2 and vec.shape[1] == 1:
                res = fast_fnc(vec[:, 0])[:, None]
            else:
                res = matrix_matvec(
                    self[np.ix_(rows, cols)], vec[rows] if transpose else vec[cols]
                )
            if out is None:
                return res
            if transpose:
                out[cols] += res
            else:
                out[rows] += res
            return out

    def matvec(self, vec, cols: np.ndarray = None, out: np.ndarray = None):
        check_matvec_out_shape(self, out)
        return self.matvec_helper(vec, None, cols, out, False)

    __array_priority__ = 12

    def transpose_matvec(
        self,
        vec: Union[np.ndarray, List],
        rows: np.ndarray = None,
        cols: np.ndarray = None,
        out: np.ndarray = None,
    ) -> np.ndarray:
        check_transpose_matvec_out_shape(self, out)
        return self.matvec_helper(vec, rows, cols, out, True)

    def get_col_stds(self, weights: np.ndarray, col_means: np.ndarray) -> np.ndarray:
        sqrt_arg = (
            transpose_square_dot_weights(
                self.data, self.indices, self.indptr, weights, weights.dtype
            )
            - col_means ** 2
        )
        # Minor floating point errors above can result in a very slightly
        # negative sqrt_arg (e.g. -5e-16). We just set those values equal to
        # zero.
        sqrt_arg[sqrt_arg < 0] = 0
        return np.sqrt(sqrt_arg)

    def astype(self, dtype, order="K", casting="unsafe", copy=True):
        return super(SparseMatrix, self).astype(dtype, casting, copy)
