import warnings
from typing import List, Optional, Tuple, Union

import numpy as np
from scipy import sparse as sps

from .categorical_matrix import CategoricalMatrix
from .dense_matrix import DenseMatrix
from .ext.split import split_col_subsets
from .matrix_base import MatrixBase
from .sparse_matrix import SparseMatrix
from .util import (
    check_matvec_out_shape,
    check_transpose_matvec_out_shape,
    set_up_rows_or_cols,
)


def split_sparse_and_dense_parts(
    arg1: sps.csc_matrix, threshold: float = 0.1
) -> Tuple[DenseMatrix, SparseMatrix, np.ndarray, np.ndarray]:
    """
    Split matrix.

    Return the dense and sparse parts of a matrix and the corresponding indices
    for each at the provided threshhold.
    """
    if not isinstance(arg1, sps.csc_matrix):
        raise TypeError(
            f"X must be of type scipy.sparse.csc_matrix or matrix.SparseMatrix,"
            f"not {type(arg1)}"
        )
    if not 0 <= threshold <= 1:
        raise ValueError("Threshold must be between 0 and 1.")
    densities = np.diff(arg1.indptr) / arg1.shape[0]
    dense_indices = np.where(densities > threshold)[0]
    sparse_indices = np.setdiff1d(np.arange(densities.shape[0]), dense_indices)

    X_dense_F = DenseMatrix(np.asfortranarray(arg1[:, dense_indices].toarray()))
    X_sparse = SparseMatrix(arg1[:, sparse_indices])
    return X_dense_F, X_sparse, dense_indices, sparse_indices


def csc_to_split(mat: sps.csc_matrix, threshold=0.1):
    """Convert a csc matrix into a split matrix at the provided threshold."""
    dense, sparse, dense_idx, sparse_idx = split_sparse_and_dense_parts(mat, threshold)
    return SplitMatrix([dense, sparse], [dense_idx, sparse_idx])


def _prepare_out_array(out: Optional[np.ndarray], out_shape, out_dtype):
    if out is None:
        out = np.zeros(out_shape, out_dtype)
    else:
        # TODO: make this a re-usable method that all the matrix classes
        # can use to check their out parameter
        if out.dtype != out_dtype:
            raise ValueError(
                f"out array is required to have dtype {out_dtype} but has"
                f"dtype {out.dtype}"
            )
    return out


def combine_matrices(matrices, indices):
    """
    Combine multiple SparseMatrix and DenseMatrix objects into a single object of each type.

    `matrices` is  and `indices` marks which columns they correspond to.
    Categorical matrices remain unmodified by this function since categorical
    matrices cannot be combined (each categorical matrix represents a single category).

    Parameters
    ----------
    matrices:
        The MatrixBase matrices to be combined.

    indices:
        The columns the each matrix corresponds to.
    """
    n_row = matrices[0].shape[0]

    for mat_type_, stack_fn in [
        (DenseMatrix, np.hstack),
        (SparseMatrix, sps.hstack),
    ]:
        this_type_matrices = [
            i for i, mat in enumerate(matrices) if isinstance(mat, mat_type_)
        ]
        if len(this_type_matrices) > 1:
            matrices[this_type_matrices[0]] = mat_type_(
                stack_fn([matrices[i] for i in this_type_matrices])
            )
            assert matrices[this_type_matrices[0]].shape[0] == n_row
            indices[this_type_matrices[0]] = np.concatenate(
                [indices[i] for i in this_type_matrices]
            )
            indices = [
                idx for i, idx in enumerate(indices) if i not in this_type_matrices[1:]
            ]
            matrices = [
                mat for i, mat in enumerate(matrices) if i not in this_type_matrices[1:]
            ]
    return matrices, indices


class SplitMatrix(MatrixBase):
    """
    A class for matrices with both sparse and dense parts.

    For real-world data that contains some dense columns and some sparse columns,
    the split representation allows for a significant speedup in matrix multiplications
    compared to representations that are entirely dense or entirely sparse.
    """

    def __init__(
        self,
        matrices: List[Union[DenseMatrix, SparseMatrix, CategoricalMatrix]],
        indices: Optional[List[np.ndarray]] = None,
    ):
        # First check that all matrices are valid types
        for _, mat in enumerate(matrices):
            if not isinstance(mat, MatrixBase):
                raise ValueError(
                    "Expected all elements of matrices to be subclasses of MatrixBase."
                )
            if isinstance(mat, SplitMatrix):
                raise ValueError("Elements of matrices cannot be SplitMatrix.")

        # Now that we know these are all MatrixBase, we can check consistent
        # shapes and dtypes.
        self.dtype = matrices[0].dtype
        n_row = matrices[0].shape[0]
        for i, mat in enumerate(matrices):
            if mat.dtype != self.dtype:
                warnings.warn(
                    "Matrices do not all have the same dtype. Dtypes are "
                    f"{[elt.dtype for elt in matrices]}."
                )
            if not mat.shape[0] == n_row:
                raise ValueError(
                    "All matrices should have the same first dimension, "
                    f"but the first matrix has first dimension {n_row} and matrix {i} has "
                    f"first dimension {mat.shape[0]}."
                )
            if len(mat.shape) != 2:
                raise ValueError("All matrices should be two dimensional.")

        if indices is None:
            indices = []
            current_idx = 0
            for mat in matrices:
                indices.append(
                    np.arange(current_idx, current_idx + mat.shape[1], dtype=np.int64)
                )
                current_idx += mat.shape[1]
            n_col = current_idx
        else:
            all_indices = np.concatenate(indices)
            n_col = len(all_indices)

            if (np.arange(n_col, dtype=np.int64) != np.sort(all_indices)).any():
                raise ValueError(
                    "Indices should contain all integers from 0 to one less than the "
                    "number of columns."
                )

        assert isinstance(indices, list)

        for i, (mat, idx) in enumerate(zip(matrices, indices)):
            if not mat.shape[1] == len(idx):
                raise ValueError(
                    f"Element {i} of indices should should have length {mat.shape[1]}, "
                    f"but it has shape {idx.shape}"
                )

        combined_matrices, combined_indices = combine_matrices(matrices, indices)

        self.matrices = combined_matrices
        self.indices = [np.asarray(elt) for elt in combined_indices]
        self.shape = (n_row, n_col)

        assert self.shape[1] > 0

    def _split_col_subsets(
        self, cols: Optional[np.ndarray]
    ) -> Tuple[List[np.ndarray], List[Optional[np.ndarray]], int]:
        """
        Return tuple of things helpful for applying column restrictions to sub-matrices.

        - subset_cols_indices
        - subset_cols
        - n_cols

        Outputs obey
            self.indices[i][subset_cols[i]] == cols[subset_cols_indices[i]]
        for all i when cols is not None, and
            mat.indices[i] == subset_cols_indices[i]
        when cols is None.
        """
        if cols is None:
            subset_cols_indices = self.indices
            subset_cols = [None for _ in range(len(self.indices))]
            return subset_cols_indices, subset_cols, self.shape[1]

        cols = set_up_rows_or_cols(cols, self.shape[1])
        return split_col_subsets(self, cols)

    def astype(self, dtype, order="K", casting="unsafe", copy=True):
        """Return SplitMatrix cast to new type."""
        if copy:
            new_matrices = [
                mat.astype(dtype=dtype, order=order, casting=casting, copy=True)
                for mat in self.matrices
            ]
            return SplitMatrix(new_matrices, self.indices)
        for i in range(len(self.matrices)):
            self.matrices[i] = self.matrices[i].astype(
                dtype=dtype, order=order, casting=casting, copy=False
            )
        return SplitMatrix(self.matrices, self.indices)

    def toarray(self) -> np.ndarray:
        """Return array representation of matrix."""
        out = np.empty(self.shape)
        for mat, idx in zip(self.matrices, self.indices):
            out[:, idx] = mat.A
        return out

    def getcol(self, i: int) -> Union[np.ndarray, sps.csr_matrix]:
        """Return matrix column at specified index."""
        # wrap-around indexing
        i %= self.shape[1]
        for mat, idx in zip(self.matrices, self.indices):
            if i in idx:
                loc = np.where(idx == i)[0][0]
                return mat.getcol(loc)
        raise RuntimeError(f"Column {i} was not found.")

    def sandwich(
        self,
        d: Union[np.ndarray, List],
        rows: np.ndarray = None,
        cols: np.ndarray = None,
    ) -> np.ndarray:
        """Perform a sandwich product: X.T @ diag(d) @ X."""
        if np.shape(d) != (self.shape[0],):
            raise ValueError
        d = np.asarray(d)

        subset_cols_indices, subset_cols, n_cols = self._split_col_subsets(cols)

        out = np.zeros((n_cols, n_cols))
        for i in range(len(self.indices)):
            idx_i = subset_cols_indices[i]
            mat_i = self.matrices[i]
            res = mat_i.sandwich(d, rows, subset_cols[i])
            if isinstance(res, sps.dia_matrix):
                out[(idx_i, idx_i)] += np.squeeze(res.data)
            else:
                out[np.ix_(idx_i, idx_i)] = res

            for j in range(i + 1, len(self.indices)):
                idx_j = subset_cols_indices[j]
                mat_j = self.matrices[j]
                res = mat_i.cross_sandwich(
                    mat_j, d, rows, subset_cols[i], subset_cols[j]
                )

                out[np.ix_(idx_i, idx_j)] = res
                out[np.ix_(idx_j, idx_i)] = res.T

        return out

    def get_col_means(self, weights: np.ndarray) -> np.ndarray:
        """Get means of columns."""
        col_means = np.empty(self.shape[1], dtype=self.dtype)
        for idx, mat in zip(self.indices, self.matrices):
            col_means[idx] = mat.get_col_means(weights)
        return col_means

    def get_col_stds(self, weights: np.ndarray, col_means: np.ndarray) -> np.ndarray:
        """Get standard deviations of columns."""
        col_stds = np.empty(self.shape[1], dtype=self.dtype)
        for idx, mat in zip(self.indices, self.matrices):
            col_stds[idx] = mat.get_col_stds(weights, col_means[idx])

        return col_stds

    def matvec(
        self, v: np.ndarray, cols: np.ndarray = None, out: np.ndarray = None
    ) -> np.ndarray:
        """Perform self[:, cols] @ other."""
        assert not isinstance(v, sps.spmatrix)
        check_matvec_out_shape(self, out)

        v = np.asarray(v)
        if v.shape[0] != self.shape[1]:
            raise ValueError(f"shapes {self.shape} and {v.shape} not aligned")

        _, subset_cols, n_cols = self._split_col_subsets(cols)

        out_shape = [self.shape[0]] + ([] if v.ndim == 1 else list(v.shape[1:]))
        out_dtype = np.result_type(self.dtype, v.dtype)
        out = _prepare_out_array(out, out_shape, out_dtype)

        for sub_cols, idx, mat in zip(subset_cols, self.indices, self.matrices):
            one = v[idx, ...]
            mat.matvec(one, sub_cols, out=out)
        return out

    def transpose_matvec(
        self,
        v: Union[np.ndarray, List],
        rows: np.ndarray = None,
        cols: np.ndarray = None,
        out: np.ndarray = None,
    ) -> np.ndarray:
        """
        Perform: self[rows, cols].T @ vec.

        self.transpose_matvec(v, rows, cols) = self[rows, cols].T @ v[rows]
        self.transpose_matvec(v, rows, cols)[i]
            = sum_{j in rows} self[j, cols[i]] v[j]
            = sum_{j in rows} sum_{mat in self.matrices} 1(cols[i] in mat)
                                                        self[j, cols[i]] v[j]
        """
        check_transpose_matvec_out_shape(self, out)

        v = np.asarray(v)
        subset_cols_indices, subset_cols, n_cols = self._split_col_subsets(cols)

        out_shape = [n_cols] + list(v.shape[1:])
        out_dtype = np.result_type(self.dtype, v.dtype)
        out_is_none = out is None
        out = _prepare_out_array(out, out_shape, out_dtype)
        if cols is not None:
            cols = np.asarray(cols, dtype=np.int32)

        for idx, sub_cols, mat in zip(subset_cols_indices, subset_cols, self.matrices):
            res = mat.transpose_matvec(v, rows=rows, cols=sub_cols)
            if out_is_none or cols is None:
                out[idx, ...] += res
            else:
                out[cols[idx], ...] += res
        return out

    def __getitem__(self, key):
        if isinstance(key, tuple):
            row, col = key
        else:
            row = key
            col = slice(None, None, None)  # all columns

        if col == slice(None, None, None):
            if isinstance(row, int):
                row = [row]

            return SplitMatrix([mat[row, :] for mat in self.matrices], self.indices)
        else:
            raise NotImplementedError(
                f"Only row indexing is supported. Index passed was {key}."
            )

    def __repr__(self):
        out = "SplitMatrix:"
        for i, mat in enumerate(self.matrices):
            out += f"\nComponent {i}:\n" + str(mat)
        return out

    __array_priority__ = 13
