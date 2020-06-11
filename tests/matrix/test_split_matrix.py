import numpy as np
import pytest
import scipy.sparse as sps

import quantcore.glm.matrix as mx
from quantcore.glm.matrix.sandwich.sandwich import csr_dense_sandwich
from quantcore.glm.matrix.split_matrix import SplitMatrix, split_sparse_and_dense_parts

N = 100


def make_X() -> np.ndarray:
    X = np.zeros((N, 4))
    X[:, 0] = 1.0
    X[:10, 1] = 0.5
    X[-20:, 2] = 0.25
    X[:, 3] = 2.0
    return X


@pytest.fixture
def X() -> np.ndarray:
    return make_X()


def test_csc_to_split(X: np.ndarray):
    for T, D, S in [(0.05, 4, 0), (0.1, 3, 1), (0.2, 2, 2), (0.3, 2, 2), (1.0, 0, 4)]:
        dense, sparse, dense_ix, sparse_ix = split_sparse_and_dense_parts(
            sps.csc_matrix(X), T
        )
        fully_dense = SplitMatrix([dense, sparse], [dense_ix, sparse_ix])
        assert fully_dense.indices[0].shape[0] == D
        assert fully_dense.indices[1].shape[0] == S


@pytest.fixture()
def split_mat() -> SplitMatrix:
    X = make_X()
    threshold = 0.1
    cat_mat = mx.CategoricalMatrix(np.random.choice(range(4), X.shape[0]))
    dense, sparse, dense_ix, sparse_ix = split_sparse_and_dense_parts(
        sps.csc_matrix(X), threshold
    )
    cat_start = 1 + max(dense_ix.max(), sparse_ix.max())
    mat = SplitMatrix(
        [dense, sparse, cat_mat],
        [dense_ix, sparse_ix, range(cat_start, cat_start + cat_mat.shape[1])],
    )
    return mat


@pytest.fixture()
def split_mat_2() -> SplitMatrix:
    """
    Initialized with multiple sparse and dense parts and no indices.
    """
    n_rows = 10
    np.random.seed(0)
    dense_1 = mx.DenseGLMDataMatrix(np.random.random((n_rows, 3)))
    sparse_1 = mx.MKLSparseMatrix(sps.random(n_rows, 3).tocsc())
    cat = mx.CategoricalMatrix(np.random.choice(range(3), n_rows))
    dense_2 = mx.DenseGLMDataMatrix(np.random.random((n_rows, 3)))
    sparse_2 = mx.MKLSparseMatrix(sps.random(n_rows, 3, density=0.5).tocsc())
    cat_2 = mx.CategoricalMatrix(np.random.choice(range(3), n_rows))
    return mx.SplitMatrix([dense_1, sparse_1, cat, dense_2, sparse_2, cat_2])


def test_init(split_mat_2: SplitMatrix):
    assert len(split_mat_2.indices) == 4
    assert len(split_mat_2.matrices) == 4
    assert (
        split_mat_2.indices[0] == np.concatenate([np.arange(3), np.arange(9, 12)])
    ).all()
    assert split_mat_2.matrices[0].shape == (10, 6)
    assert split_mat_2.matrices[1].shape == (10, 6)
    assert split_mat_2.matrices[2].shape == (10, 3)


def test_sandwich_sparse_dense(X: np.ndarray):
    np.random.seed(0)
    n, k = X.shape
    d = np.random.random((n,))
    A = sps.random(n, 2).tocsr()
    result = csr_dense_sandwich(A, X, d)
    expected = A.T.A @ np.diag(d) @ X
    np.testing.assert_allclose(result, expected)


def test_sandwich(split_mat: SplitMatrix):
    for i in range(10):
        v = np.random.rand(split_mat.shape[0])
        y1 = split_mat.sandwich(v)
        y2 = (split_mat.A.T * v[None, :]) @ split_mat.A
        np.testing.assert_allclose(y1, y2, atol=1e-12)


def test_sandwich_many_types():
    n_rows = 10
    np.random.seed(0)
    dense_1 = mx.DenseGLMDataMatrix(np.random.random((n_rows, 3)))
    sparse = mx.MKLSparseMatrix(sps.random(n_rows, 3).tocsc())
    cat = mx.CategoricalMatrix(np.random.choice(range(3), n_rows))
    dense_2 = mx.DenseGLMDataMatrix(np.random.random((n_rows, 3)))
    mat = mx.SplitMatrix([dense_1, sparse, cat, dense_2])
    d = np.random.random(n_rows)
    res = mat.sandwich(d)
    expected = (mat.A.T * d[None, :]) @ mat.A
    np.testing.assert_allclose(res, expected)
