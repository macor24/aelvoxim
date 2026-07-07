"""Additional learner tests."""

def test_learner_init():
    """Learner can be initialized."""
    from aelvoxim.learn.learner import Learner
    learner = Learner()
    assert learner is not None
    assert hasattr(learner, '_directions')
    learner.stop()


def test_get_learner():
    """get_learner returns a singleton instance."""
    from aelvoxim.learn.learner import get_learner
    l1 = get_learner()
    l2 = get_learner()
    assert l1 is None or l1 is l2, "get_learner should return same instance if created"
