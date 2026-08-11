"""Tests for FunctionClassifier."""

from ingestion.classifier import FunctionClassifier


def test_classifier_folly_namespace() -> None:
    classifier = FunctionClassifier()
    source, lib = classifier.classify("folly::futures::detail::FutureImpl::then")
    assert source == "open_source"
    assert lib == "folly"


def test_classifier_customer_custom() -> None:
    classifier = FunctionClassifier()
    source, lib = classifier.classify("CustomerCustom::featureCalc")
    assert source == "customer_custom"
    assert lib == "custom"


def test_classifier_no_false_positives() -> None:
    classifier = FunctionClassifier()
    # "MyFollyWrapper" should NOT match folly namespace pattern "folly::"
    source, _lib = classifier.classify("MyFollyWrapper::process")
    assert source == "customer_custom"


def test_classifier_taskflow_alias() -> None:
    classifier = FunctionClassifier()
    source, lib = classifier.classify("tf::ParallelFor::dispatch")
    assert source == "open_source"
    assert lib == "taskflow"


def test_classifier_brpc() -> None:
    classifier = FunctionClassifier()
    source, lib = classifier.classify("brpc::Controller::onResponse")
    assert source == "open_source"
    assert lib == "brpc"


def test_classifier_tensorflow() -> None:
    """tensorflow:: (the C++ TF namespace) is open source, distinct from tf:: (taskflow)."""
    classifier = FunctionClassifier()
    source, lib = classifier.classify("tensorflow::ops::MatMul")
    assert source == "open_source"
    assert lib == "tensorflow"


def test_classifier_tf_still_taskflow_not_tensorflow() -> None:
    """tf:: stays taskflow; tensorflow:: is its own library. They don't cross-match."""
    classifier = FunctionClassifier()
    assert classifier.classify("tf::ParallelFor::dispatch") == ("open_source", "taskflow")
    assert classifier.classify("tensorflow::Tensor") == ("open_source", "tensorflow")


def test_classifier_memoizes_repeated_names() -> None:
    """classify caches per name so repeated frames skip the regex sweep."""
    classifier = FunctionClassifier()
    name = "folly::futures::detail::FutureImpl::then"
    first = classifier.classify(name)
    second = classifier.classify(name)
    assert first == second == ("open_source", "folly")
    assert classifier._cache[name] == ("open_source", "folly")
    assert len(classifier._cache) == 1
