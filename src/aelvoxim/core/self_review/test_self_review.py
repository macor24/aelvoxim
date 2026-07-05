import sys
sys.path.insert(0, r"C:\Aelvoxim\src\metacore\core")

from self_review_system import SelfReviewSystem

class MockMemory:
    def store(self, data):
        print(f"[MockMemory] Stored: {data.get('conversation_id')}")
    
    def query(self, filter=None, limit=10, sort_by="timestamp", sort_order="desc"):
        return []

system = SelfReviewSystem(memory_interface=MockMemory())

result = system.review_conversation(
    conversation_id="test_001",
    user_question="What is a self-assessment system?",
    assistant_responses=["A self-assessment system evaluates AI response quality."],
    user_feedback="good"
)

print("SelfReviewSystem initialized successfully")
print("Review Result:")
print(f"  Overall score: {result['overall_score']}")
print(f"  Dimension scores: {result['scores']}")
print(f"  Weaknesses: {result['weaknesses']}")
print(f"  Improvement plan: {result['improvement_plan']}")
