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
    user_question="什么是自我评估系统？",
    assistant_responses=["自我评估系统是一种用于衡量AI回答质量的机制。"],
    user_feedback="不错"
)

print("SelfReviewSystem 初始化成功")
print("Review Result:")
print(f"  总体评分: {result['overall_score']}")
print(f"  各维度评分: {result['scores']}")
print(f"  薄弱环节: {result['weaknesses']}")
print(f"  改进计划: {result['improvement_plan']}")
