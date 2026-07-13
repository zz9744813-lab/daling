"""应用服务层包。

包含跨多层的业务服务，如连续生产服务（continuous_production）。
"""

from app.services.autonomous_learning import AutonomousLearningService
from app.services.quality_ledger import QualityLedger, fingerprint_issue

__all__ = ["AutonomousLearningService", "QualityLedger", "fingerprint_issue"]
