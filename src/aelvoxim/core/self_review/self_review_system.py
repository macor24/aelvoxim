import json
from datetime import datetime
from typing import Dict, List, Optional

class SelfReviewSystem:
    def __init__(self, memory_interface):
        self.memory = memory_interface
        self.dimensions = {
            "accuracy": {"weight": 0.35, "threshold": 70},
            "completeness": {"weight": 0.25, "threshold": 70},
            "coherence": {"weight": 0.20, "threshold": 70},
            "user_satisfaction": {"weight": 0.20, "threshold": 70}
        }
    
    def review_conversation(self, conversation_id: str, 
                           user_question: str, 
                           assistant_responses: List[str],
                           user_feedback: Optional[str] = None) -> Dict:
        """对一次对话执行全面评估"""
        
        scores = {}
        
        # 评估各个维度
        scores["accuracy"] = self._evaluate_accuracy(assistant_responses)
        scores["completeness"] = self._evaluate_completeness(user_question, assistant_responses)
        scores["coherence"] = self._evaluate_coherence(assistant_responses)
        scores["user_satisfaction"] = self._evaluate_satisfaction(user_feedback)
        
        # 计算总分
        overall_score = sum(
            scores[dim] * config["weight"] 
            for dim, config in self.dimensions.items()
        )
        
        # 识别薄弱环节
        weaknesses = self._identify_weaknesses(scores)
        
        # 生成改进计划
        improvement_plan = self._generate_improvement_plan(weaknesses)
        
        # 构建评估报告
        report = {
            "type": "self_review",
            "conversation_id": conversation_id,
            "timestamp": datetime.now().isoformat(),
            "scores": scores,
            "overall_score": round(overall_score, 1),
            "weaknesses": weaknesses,
            "improvement_plan": improvement_plan,
            "status": "pending_review"
        }
        
        # 写入记忆
        self.memory.store(report)
        
        return report
    
    def _evaluate_accuracy(self, responses: List[str]) -> float:
        """评估准确性 - 基于知识引用和事实一致性"""
        # 实现逻辑：检查回答中的事实性陈述与知识库的匹配度
        # 初期可以简化为：检查是否有明确的知识引用
        # 后续可以升级为：调用知识检索接口进行交叉验证
        base_score = 80.0
        
        # 检查是否有知识引用
        has_citations = any(
            "根据" in resp or "参考" in resp or "来源" in resp 
            for resp in responses
        )
        
        if not has_citations:
            base_score -= 10
        
        # 检查是否有矛盾陈述
        # 此处需要更复杂的逻辑，初期先保留
        
        return base_score
    
    def _evaluate_completeness(self, question: str, responses: List[str]) -> float:
        """评估完整性 - 基于问题覆盖度"""
        # 实现逻辑：分析问题中的关键点，检查回答是否逐一覆盖
        # 初期可以简化为：检查回答长度和问题复杂度的比例
        # 后续可以升级为：使用NLP提取问题中的子问题
        
        question_length = len(question)
        total_response_length = sum(len(r) for r in responses)
        
        # 简单的比例评估
        ratio = total_response_length / max(question_length, 1)
        
        if ratio < 3:
            return 50.0  # 回答太短，覆盖不足
        elif ratio < 5:
            return 70.0  # 基本覆盖
        elif ratio < 10:
            return 85.0  # 覆盖良好
        else:
            return 90.0  # 覆盖充分
    
    def _evaluate_coherence(self, responses: List[str]) -> float:
        """评估逻辑连贯性"""
        # 实现逻辑：检查回答内部的逻辑一致性
        # 初期可以简化为：检查是否有逻辑连接词，是否有跳跃
        # 后续可以升级为：使用逻辑推理验证
        
        base_score = 80.0
        
        # 检查是否有逻辑连接词
        logical_markers = ["因为", "所以", "因此", "但是", "然而", "首先", "其次", "最后"]
        has_logical_structure = any(
            any(marker in resp for marker in logical_markers)
            for resp in responses
        )
        
        if not has_logical_structure:
            base_score -= 15
        
        return base_score
    
    def _evaluate_satisfaction(self, feedback: Optional[str]) -> float:
        """评估用户满意度"""
        if not feedback:
            return 75.0  # 没有反馈，给中等分数
        
        # 简单的情感分析
        positive_words = ["好", "对", "正确", "谢谢", "完美", "不错"]
        negative_words = ["错", "不对", "差", "不好"]
        
        positive_count = sum(1 for word in positive_words if word in feedback)
        negative_count = sum(1 for word in negative_words if word in feedback)
        
        if negative_count > positive_count:
            return 40.0
        elif positive_count > negative_count:
            return 90.0
        else:
            return 65.0
    
    def _identify_weaknesses(self, scores: Dict[str, float]) -> List[Dict]:
        """识别薄弱环节"""
        weaknesses = []
        
        for dim, score in scores.items():
            threshold = self.dimensions[dim]["threshold"]
            if score < threshold:
                weaknesses.append({
                    "dimension": dim,
                    "score": score,
                    "threshold": threshold,
                    "gap": threshold - score,
                    "reason": f"评分{score}低于阈值{threshold}"
                })
        
        return weaknesses
    
    def _generate_improvement_plan(self, weaknesses: List[Dict]) -> List[Dict]:
        """生成改进计划"""
        plan = []
        
        for weakness in weaknesses:
            dim = weakness["dimension"]
            gap = weakness["gap"]
            
            if dim == "accuracy":
                action = {
                    "action": "knowledge_audit",
                    "target": "检查知识引用来源，补充可靠资料",
                    "priority": "high" if gap > 20 else "medium"
                }
            elif dim == "completeness":
                action = {
                    "action": "search_supplement",
                    "target": "搜索补充缺失的信息点",
                    "priority": "high" if gap > 20 else "medium"
                }
            elif dim == "coherence":
                action = {
                    "action": "logic_training",
                    "target": "调整推理策略，增加逻辑连接",
                    "priority": "medium"
                }
            elif dim == "user_satisfaction":
                action = {
                    "action": "feedback_analysis",
                    "target": "分析用户反馈，调整回答风格",
                    "priority": "high" if gap > 20 else "low"
                }
            
            plan.append(action)
        
        return plan
    
    def get_review_history(self, limit: int = 10) -> List[Dict]:
        """获取历史评估记录"""
        return self.memory.query(
            filter={"type": "self_review"},
            limit=limit,
            sort_by="timestamp",
            sort_order="desc"
        )
    
    def get_improvement_status(self) -> Dict:
        """获取改进进度概览"""
        reviews = self.get_review_history(limit=50)
        
        total_reviews = len(reviews)
        completed_improvements = sum(
            1 for r in reviews 
            if r.get("status") == "improved"
        )
        
        return {
            "total_reviews": total_reviews,
            "completed_improvements": completed_improvements,
            "improvement_rate": round(
                completed_improvements / max(total_reviews, 1) * 100, 1
            ),
            "average_score": round(
                sum(r["overall_score"] for r in reviews) / max(total_reviews, 1), 1
            ),
        }
