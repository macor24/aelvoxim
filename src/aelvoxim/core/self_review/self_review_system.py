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
        """Comprehensive evaluation of a conversation."""
        
        scores = {}
        
        # Evaluate each dimension
        scores["accuracy"] = self._evaluate_accuracy(assistant_responses)
        scores["completeness"] = self._evaluate_completeness(user_question, assistant_responses)
        scores["coherence"] = self._evaluate_coherence(assistant_responses)
        scores["user_satisfaction"] = self._evaluate_satisfaction(user_feedback)
        
        # Compute weighted total
        overall_score = sum(
            scores[dim] * config["weight"] 
            for dim, config in self.dimensions.items()
        )
        
        # Identify weak dimensions
        weaknesses = self._identify_weaknesses(scores)
        
        # Generate improvement plan
        improvement_plan = self._generate_improvement_plan(weaknesses)
        
        # Build evaluation report
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
        
        # Persist to memory
        self.memory.store(report)
        
        return report
    
    def _evaluate_accuracy(self, responses: List[str]) -> float:
        """Evaluate accuracy — knowledge citation and factual consistency."""
        # Check factual statements against knowledge base
        # Simplified: check for explicit knowledge citations
        # Future: cross-validate via knowledge retrieval API
        base_score = 80.0
        
        # Check for citations
        has_citations = any(
            "根据" in resp or "参考" in resp or "来源" in resp 
            for resp in responses
        )
        
        if not has_citations:
            base_score -= 10
        
        # Check for contradictory statements
        # TODO: more sophisticated logic, placeholder for now
        
        return base_score
    
    def _evaluate_completeness(self, question: str, responses: List[str]) -> float:
        """Evaluate completeness — question coverage."""
        # Check if all key points in the question are addressed
        # Simplified: ratio of response length to question complexity
        # Future: NLP-based sub-question extraction
        
        question_length = len(question)
        total_response_length = sum(len(r) for r in responses)
        
        # Simple length ratio heuristic
        ratio = total_response_length / max(question_length, 1)
        
        if ratio < 3:
            return 50.0  # Too short, insufficient coverage
        elif ratio < 5:
            return 70.0  # Basic coverage
        elif ratio < 10:
            return 85.0  # Good coverage
        else:
            return 90.0  # Full coverage
    
    def _evaluate_coherence(self, responses: List[str]) -> float:
        """Evaluate logical coherence."""
        # Check internal logical consistency of responses
        # Simplified: check for logical connectors, no jumps
        # Future: formal logic verification
        
        base_score = 80.0
        
        # Check for logical connectors
        logical_markers = ["because", "therefore", "however", "first", "second", "finally"]
        has_logical_structure = any(
            any(marker in resp for marker in logical_markers)
            for resp in responses
        )
        
        if not has_logical_structure:
            base_score -= 15
        
        return base_score
    
    def _evaluate_satisfaction(self, feedback: Optional[str]) -> float:
        """Evaluate user satisfaction from feedback."""
        if not feedback:
            return 75.0  # No feedback, default to medium score
        
        # Simple sentiment analysis
        positive_words = ["good", "correct", "right", "thanks", "perfect", "nice"]
        negative_words = ["wrong", "incorrect", "bad", "poor"]
        
        positive_count = sum(1 for word in positive_words if word in feedback)
        negative_count = sum(1 for word in negative_words if word in feedback)
        
        if negative_count > positive_count:
            return 40.0
        elif positive_count > negative_count:
            return 90.0
        else:
            return 65.0
    
    def _identify_weaknesses(self, scores: Dict[str, float]) -> List[Dict]:
        """Identify dimensions below threshold."""
        weaknesses = []
        
        for dim, score in scores.items():
            threshold = self.dimensions[dim]["threshold"]
            if score < threshold:
                weaknesses.append({
                    "dimension": dim,
                    "score": score,
                    "threshold": threshold,
                    "gap": threshold - score,
                    "reason": f"score {score} below threshold {threshold}"
                })
        
        return weaknesses
    
    def _generate_improvement_plan(self, weaknesses: List[Dict]) -> List[Dict]:
        """Generate improvement actions for weaknesses."""
        plan = []
        
        for weakness in weaknesses:
            dim = weakness["dimension"]
            gap = weakness["gap"]
            
            if dim == "accuracy":
                action = {
                    "action": "knowledge_audit",
                    "target": "Audit knowledge citations, supplement reliable sources",
                    "priority": "high" if gap > 20 else "medium"
                }
            elif dim == "completeness":
                action = {
                    "action": "search_supplement",
                    "target": "Search and supplement missing information",
                    "priority": "high" if gap > 20 else "medium"
                }
            elif dim == "coherence":
                action = {
                    "action": "logic_training",
                    "target": "Adjust reasoning strategy, add logical connectors",
                    "priority": "medium"
                }
            elif dim == "user_satisfaction":
                action = {
                    "action": "feedback_analysis",
                    "target": "Analyze user feedback, adjust response style",
                    "priority": "high" if gap > 20 else "low"
                }
            
            plan.append(action)
        
        return plan
    
    def get_review_history(self, limit: int = 10) -> List[Dict]:
        """Get past review history."""
        return self.memory.query(
            filter={"type": "self_review"},
            limit=limit,
            sort_by="timestamp",
            sort_order="desc"
        )
    
    def get_improvement_status(self) -> Dict:
        """Get improvement progress overview."""
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
