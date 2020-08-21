import warnings
from typing import List, Optional, Union

import numpy as np
import pandas as pd
import pytest
from scipy import sparse as sps

import quantcore.matrix as mx


def base_array(order="F") -> np.ndarray:
    return np.array([[0, 0], [0, -1.0], [0, 2.0]], order=order)


def dense_matrix_F() -> mx.DenseMatrix:
    return mx.DenseMatrix(base_array())


def dense_matrix_C() -> mx.DenseMatrix:
    return mx.DenseMatrix(base_array(order="C"))


def dense_matrix_not_writeable() -> mx.DenseMatrix:
    mat = dense_matrix_F()
    mat.setflags(write=False)
    return mat


def sparse_matrix() -> mx.SparseMatrix:
    return mx.SparseMatrix(sps.csc_matrix(base_array()))


def sparse_matrix_64() -> mx.SparseMatrix:
    csc = sps.csc_matrix(base_array())
    mat = mx.SparseMatrix(
        (csc.data, csc.indices.astype(np.int64), csc.indptr.astype(np.int64))
    )
    return mat


def categorical_matrix():
    vec = [1, 0, 1]
    return mx.CategoricalMatrix(vec)


def get_unscaled_matrices() -> List[
    Union[mx.DenseMatrix, mx.SparseMatrix, mx.CategoricalMatrix]
]:
    return [
        dense_matrix_F(),
        dense_matrix_C(),
        dense_matrix_not_writeable(),
        sparse_matrix(),
        sparse_matrix_64(),
        categorical_matrix(),
    ]


def complex_split_matrix():
    return mx.SplitMatrix(get_unscaled_matrices())


def shift_complex_split_matrix():
    mat = complex_split_matrix()
    np.random.seed(0)
    return mx.StandardizedMatrix(mat, np.random.random(mat.shape[1]))


def shift_scale_complex_split_matrix():
    mat = complex_split_matrix()
    np.random.seed(0)
    return mx.StandardizedMatrix(
        mat, np.random.random(mat.shape[1]), np.random.random(mat.shape[1])
    )


def get_all_matrix_base_subclass_mats():
    return get_unscaled_matrices() + [complex_split_matrix()]


def get_standardized_shifted_matrices():
    return [mx.StandardizedMatrix(elt, [0.3, 2]) for elt in get_unscaled_matrices()] + [
        shift_complex_split_matrix()
    ]


def get_standardized_shifted_scaled_matrices():
    return [
        mx.StandardizedMatrix(elt, [0.3, 0.2], [0.6, 1.67])
        for elt in get_unscaled_matrices()
    ] + [shift_scale_complex_split_matrix()]


def get_matrices():
    return (
        get_all_matrix_base_subclass_mats()
        + get_standardized_shifted_matrices()
        + get_standardized_shifted_matrices()
    )


@pytest.mark.parametrize("mat", get_matrices())
@pytest.mark.parametrize("i", [1, -2])
def test_getcol(mat: Union[mx.MatrixBase, mx.StandardizedMatrix], i):
    col = mat.getcol(i)

    if not isinstance(col, np.ndarray):
        col = col.A
    np.testing.assert_almost_equal(col, mat.A[:, [i]])


@pytest.mark.parametrize("mat", get_all_matrix_base_subclass_mats())
def test_to_array_matrix_base(mat: mx.MatrixBase):
    assert isinstance(mat.A, np.ndarray)
    if isinstance(mat, mx.CategoricalMatrix):
        expected = np.array([[0, 1], [1, 0], [0, 1]])
    elif isinstance(mat, mx.SplitMatrix):
        expected = np.hstack([elt.A for elt in mat.matrices])
    else:
        expected = base_array()
    np.testing.assert_allclose(mat.A, expected)


@pytest.mark.parametrize(
    "mat",
    get_standardized_shifted_matrices() + get_standardized_shifted_scaled_matrices(),
)
def test_to_array_standardized_mat(mat: mx.StandardizedMatrix):
    assert isinstance(mat.A, np.ndarray)
    true_mat_part = mat.mat.A
    if mat.mult is not None:
        true_mat_part = mat.mult[None, :] * mat.mat.A
    np.testing.assert_allclose(mat.A, true_mat_part + mat.shift)


@pytest.mark.parametrize("mat", get_matrices())
@pytest.mark.parametrize(
    "other_type", [lambda x: x, np.asarray, mx.DenseMatrix],
)
@pytest.mark.parametrize("cols", [None, np.arange(1, dtype=np.int32)])
@pytest.mark.parametrize("other_shape", [[], [1], [2]])
def test_dot(
    mat: Union[mx.MatrixBase, mx.StandardizedMatrix], other_type, cols, other_shape
):
    n_row = mat.shape[1]
    shape = [n_row] + other_shape
    other_as_list = np.random.random(shape).tolist()
    other = other_type(other_as_list)

    def is_split_with_cat_part(x):
        return isinstance(x, mx.SplitMatrix) and any(
            isinstance(elt, mx.CategoricalMatrix) for elt in x.matrices
        )

    has_categorical_component = (
        isinstance(mat, mx.CategoricalMatrix)
        or is_split_with_cat_part(mat)
        or (
            isinstance(mat, mx.StandardizedMatrix)
            and (
                isinstance(mat.mat, mx.CategoricalMatrix)
                or is_split_with_cat_part(mat.mat)
            )
        )
    )

    if has_categorical_component and len(shape) > 1:
        with pytest.raises(NotImplementedError, match="only implemented for 1d"):
            mat.dot(other, cols)
    else:
        res = mat.dot(other, cols)

        mat_subset, vec_subset = process_mat_vec_subsets(mat, other, None, cols, cols)
        expected = mat_subset.dot(vec_subset)

        np.testing.assert_allclose(res, expected)
        assert isinstance(res, np.ndarray)

        if isinstance(mat, mx.CategoricalMatrix):
            res2 = np.zeros_like(res)
            mat.vec_plus_matvec(other, res2, cols)
            np.testing.assert_allclose(res2, expected)
            assert isinstance(res2, np.ndarray)

        if cols is None:
            res2 = mat @ other
            np.testing.assert_allclose(res2, expected)


def process_mat_vec_subsets(mat, vec, mat_rows, mat_cols, vec_idxs):
    mat_subset = mat.A
    vec_subset = vec
    if mat_rows is not None:
        mat_subset = mat_subset[mat_rows, :]
    if mat_cols is not None:
        mat_subset = mat_subset[:, mat_cols]
    if vec_idxs is not None:
        vec_subset = np.array(vec_subset)[vec_idxs]
    return mat_subset, vec_subset


@pytest.mark.parametrize("mat", get_matrices())
@pytest.mark.parametrize(
    "other_type", [lambda x: x, np.array, mx.DenseMatrix],
)
@pytest.mark.parametrize("rows", [None, np.arange(2, dtype=np.int32)])
@pytest.mark.parametrize("cols", [None, np.arange(1, dtype=np.int32)])
def test_transpose_dot(
    mat: Union[mx.MatrixBase, mx.StandardizedMatrix], other_type, rows, cols
):
    other_as_list = [3.0, -0.1, 0]
    other = other_type(other_as_list)
    assert np.shape(other)[0] == mat.shape[0]
    res = mat.transpose_dot(other, rows, cols)

    mat_subset, vec_subset = process_mat_vec_subsets(
        mat, other_as_list, rows, cols, rows
    )
    expected = mat_subset.T.dot(vec_subset)
    np.testing.assert_allclose(res, expected)
    assert isinstance(res, np.ndarray)


@pytest.mark.parametrize(
    "mat_i, mat_j",
    [
        (dense_matrix_C(), sparse_matrix()),
        (dense_matrix_C(), sparse_matrix_64()),
        (dense_matrix_C(), categorical_matrix()),
        (dense_matrix_F(), sparse_matrix()),
        (dense_matrix_F(), sparse_matrix_64()),
        (dense_matrix_F(), categorical_matrix()),
        (dense_matrix_not_writeable(), sparse_matrix()),
        (dense_matrix_not_writeable(), sparse_matrix_64()),
        (dense_matrix_not_writeable(), categorical_matrix()),
        (sparse_matrix(), dense_matrix_C()),
        (sparse_matrix(), dense_matrix_F()),
        (sparse_matrix(), dense_matrix_not_writeable()),
        (sparse_matrix(), categorical_matrix()),
        (sparse_matrix_64(), dense_matrix_C()),
        (sparse_matrix_64(), dense_matrix_F()),
        (sparse_matrix_64(), dense_matrix_not_writeable()),
        (sparse_matrix_64(), categorical_matrix()),
        (categorical_matrix(), dense_matrix_C()),
        (categorical_matrix(), dense_matrix_F()),
        (categorical_matrix(), dense_matrix_not_writeable()),
        (categorical_matrix(), sparse_matrix()),
        (categorical_matrix(), sparse_matrix_64()),
        (categorical_matrix(), categorical_matrix()),
    ],
)
@pytest.mark.parametrize("rows", [None, np.arange(2, dtype=np.int32)])
@pytest.mark.parametrize("L_cols", [None, np.arange(1, dtype=np.int32)])
@pytest.mark.parametrize("R_cols", [None, np.arange(1, dtype=np.int32)])
def test_cross_sandwich(
    mat_i: Union[mx.DenseMatrix, mx.SparseMatrix, mx.CategoricalMatrix],
    mat_j: Union[mx.DenseMatrix, mx.SparseMatrix, mx.CategoricalMatrix],
    rows: Optional[np.ndarray],
    L_cols: Optional[np.ndarray],
    R_cols: Optional[np.ndarray],
):
    assert mat_i.shape[0] == mat_j.shape[0]
    d = np.random.random(mat_i.shape[0])
    mat_i_, _ = process_mat_vec_subsets(mat_i, None, rows, L_cols, None)
    mat_j_, d_ = process_mat_vec_subsets(mat_j, d, rows, R_cols, rows)
    expected = mat_i_.T @ np.diag(d_) @ mat_j_
    res = mat_i.cross_sandwich(mat_j, d, rows, L_cols, R_cols)
    np.testing.assert_almost_equal(res, expected)


@pytest.mark.parametrize("mat", get_matrices())
@pytest.mark.parametrize(
    "vec_type", [lambda x: x, np.array, mx.DenseMatrix],
)
@pytest.mark.parametrize("rows", [None, np.arange(2, dtype=np.int32)])
@pytest.mark.parametrize("cols", [None, np.arange(1, dtype=np.int32)])
def test_self_sandwich(
    mat: Union[mx.MatrixBase, mx.StandardizedMatrix], vec_type, rows, cols
):
    vec_as_list = [3, 0.1, 1]
    vec = vec_type(vec_as_list)
    res = mat.sandwich(vec, rows, cols)

    mat_subset, vec_subset = process_mat_vec_subsets(mat, vec_as_list, rows, cols, rows)
    expected = mat_subset.T @ np.diag(vec_subset) @ mat_subset
    if sps.issparse(res):
        res = res.A
    np.testing.assert_allclose(res, expected)


@pytest.mark.parametrize("rows", [None, np.arange(2, dtype=np.int32)])
@pytest.mark.parametrize("cols", [None, np.arange(1, dtype=np.int32)])
def test_split_sandwich(rows: Optional[np.ndarray], cols: Optional[np.ndarray]):
    mat = complex_split_matrix()
    d = np.random.random(mat.shape[0])
    result = mat.sandwich(d, rows=rows, cols=cols)

    mat_as_dense = mat.A
    d_rows = d
    if rows is not None:
        mat_as_dense = mat_as_dense[rows, :]
        d_rows = d[rows]
    if cols is not None:
        mat_as_dense = mat_as_dense[:, cols]

    expected = mat_as_dense.T @ np.diag(d_rows) @ mat_as_dense
    np.testing.assert_almost_equal(result, expected)


@pytest.mark.parametrize(
    "mat",
    [
        dense_matrix_F(),
        dense_matrix_C(),
        dense_matrix_not_writeable(),
        sparse_matrix(),
        sparse_matrix_64(),
    ],
)
def test_transpose(mat):
    res = mat.T.A
    expected = mat.A.T
    assert res.shape == (mat.shape[1], mat.shape[0])
    np.testing.assert_allclose(res, expected)


@pytest.mark.parametrize("mat", get_matrices())
@pytest.mark.parametrize(
    "vec_type", [lambda x: x, np.array, mx.DenseMatrix],
)
def test_rmatmul(mat: Union[mx.MatrixBase, mx.StandardizedMatrix], vec_type):
    vec_as_list = [3.0, -0.1, 0]
    vec = vec_type(vec_as_list)
    res = mat.__rmatmul__(vec)
    res2 = vec @ mat
    expected = vec_as_list @ mat.A
    np.testing.assert_allclose(res, expected)
    np.testing.assert_allclose(res2, expected)
    assert isinstance(res, np.ndarray)


@pytest.mark.parametrize("mat", get_matrices())
def test_dot_raises(mat: Union[mx.MatrixBase, mx.StandardizedMatrix]):
    with pytest.raises(ValueError):
        mat.dot(np.ones(11))


@pytest.mark.parametrize("mat", get_matrices())
@pytest.mark.parametrize("dtype", [np.float64, np.float32])
def test_astype(mat: Union[mx.MatrixBase, mx.StandardizedMatrix], dtype):
    new_mat = mat.astype(dtype)
    assert np.issubdtype(new_mat.dtype, dtype)
    vec = np.zeros(mat.shape[1], dtype=dtype)
    res = new_mat.dot(vec)
    assert res.dtype == new_mat.dtype


@pytest.mark.parametrize("mat", get_all_matrix_base_subclass_mats())
def test_get_col_means(mat: mx.MatrixBase):
    weights = np.random.random(mat.shape[0])
    # TODO: make weights sum to 1 within functions
    weights /= weights.sum()
    means = mat.get_col_means(weights)
    expected = mat.A.T.dot(weights)
    np.testing.assert_allclose(means, expected)


@pytest.mark.parametrize("mat", get_all_matrix_base_subclass_mats())
def test_get_col_means_unweighted(mat: mx.MatrixBase):
    weights = np.ones(mat.shape[0])
    # TODO: make weights sum to 1 within functions
    weights /= weights.sum()
    means = mat.get_col_means(weights)
    expected = mat.A.mean(0)
    np.testing.assert_allclose(means, expected)


@pytest.mark.parametrize("mat", get_all_matrix_base_subclass_mats())
def test_get_col_stds(mat: mx.MatrixBase):
    weights = np.random.random(mat.shape[0])
    # TODO: make weights sum to 1
    weights /= weights.sum()
    means = mat.get_col_means(weights)
    expected = np.sqrt((mat.A ** 2).T.dot(weights) - means ** 2)
    stds = mat.get_col_stds(weights, means)
    np.testing.assert_allclose(stds, expected)


@pytest.mark.parametrize("mat", get_unscaled_matrices())
def test_get_col_stds_unweighted(mat: mx.MatrixBase):
    weights = np.ones(mat.shape[0])
    # TODO: make weights sum to 1
    weights /= weights.sum()
    means = mat.get_col_means(weights)
    expected = mat.A.std(0)
    stds = mat.get_col_stds(weights, means)
    np.testing.assert_allclose(stds, expected)


@pytest.mark.parametrize("mat", get_unscaled_matrices())
@pytest.mark.parametrize("center_predictors", [False, True])
@pytest.mark.parametrize("scale_predictors", [False, True])
def test_standardize(
    mat: mx.MatrixBase, center_predictors: bool, scale_predictors: bool
):
    asarray = mat.A.copy()
    weights = np.random.rand(mat.shape[0])
    weights /= weights.sum()

    true_means = asarray.T.dot(weights)
    true_sds = np.sqrt((asarray ** 2).T.dot(weights) - true_means ** 2)

    standardized, means, stds = mat.standardize(
        weights, center_predictors, scale_predictors
    )
    assert isinstance(standardized, mx.StandardizedMatrix)
    assert isinstance(standardized.mat, type(mat))
    if center_predictors:
        np.testing.assert_allclose(standardized.transpose_dot(weights), 0, atol=1e-11)
        np.testing.assert_allclose(means, asarray.T.dot(weights))
    else:
        np.testing.assert_almost_equal(means, 0)

    if scale_predictors:
        np.testing.assert_allclose(stds, true_sds)
    else:
        assert stds is None

    expected_sds = true_sds if scale_predictors else np.ones_like(true_sds)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        one_over_sds = np.nan_to_num(1 / expected_sds)

    expected_mat = asarray * one_over_sds
    if center_predictors:
        expected_mat -= true_means * one_over_sds
    np.testing.assert_allclose(standardized.A, expected_mat)

    unstandardized = standardized.unstandardize()
    assert isinstance(unstandardized, type(mat))
    np.testing.assert_allclose(unstandardized.A, asarray)


@pytest.mark.parametrize("mat", get_matrices())
def test_indexing_int_row(mat: Union[mx.MatrixBase, mx.StandardizedMatrix]):
    res = mat[0, :]
    if not isinstance(res, np.ndarray):
        res = res.A
    expected = mat.A[0, :]
    np.testing.assert_allclose(np.squeeze(res), expected)


@pytest.mark.parametrize("mat", get_matrices())
def test_indexing_range_row(mat: Union[mx.MatrixBase, mx.StandardizedMatrix]):
    res = mat[0:2, :]
    if not isinstance(res, np.ndarray):
        res = res.A
    expected = mat.A[0:2, :]
    np.testing.assert_allclose(np.squeeze(res), expected)


def test_pandas_to_matrix():
    n_rows = 50
    dense_column = np.linspace(-10, 10, num=n_rows, dtype=np.float64)
    dense_column_with_lots_of_zeros = dense_column.copy()
    dense_column_with_lots_of_zeros[:44] = 0.0
    sparse_column = np.zeros(n_rows, dtype=np.float64)
    sparse_column[0] = 1.0
    cat_column_lowdim = np.tile(["a", "b"], n_rows // 2)
    cat_column_highdim = np.arange(n_rows)

    dense_ser = pd.Series(dense_column)
    lowdense_ser = pd.Series(dense_column_with_lots_of_zeros)
    sparse_ser = pd.Series(sparse_column, dtype=pd.SparseDtype("float", 0.0))
    cat_ser_lowdim = pd.Categorical(cat_column_lowdim)
    cat_ser_highdim = pd.Categorical(cat_column_highdim)

    df = pd.DataFrame(
        data={
            "d": dense_ser,
            "ds": lowdense_ser,
            "s": sparse_ser,
            "cl": cat_ser_lowdim,
            "ch": cat_ser_highdim,
        }
    )

    mat = mx.from_pandas(df, dtype=np.float64, sparse_threshold=0.3, cat_threshold=4)

    assert mat.shape == (n_rows, n_rows + 5)
    assert len(mat.matrices) == 3
    assert isinstance(mat, mx.SplitMatrix)

    nb_col_by_type = {
        mx.DenseMatrix: 3,  # includes low-dimension categorical
        mx.SparseMatrix: 2,  # sparse column
        mx.CategoricalMatrix: n_rows,
    }
    for submat in mat.matrices:
        assert submat.shape[1] == nb_col_by_type[type(submat)]

    # Prevent a regression where the column type of sparsified dense columns
    # was being changed in place.
    assert df["ds"].dtype == np.float64
