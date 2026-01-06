from sqlalchemy import Column, DateTime, String, Boolean
from datetime import datetime
from flask_login import current_user

class SoftDeleteMixin:
    """
    Mixin for soft-delete functionality on critical entities.
    Prevents hard deletes that would cause data inconsistency.
    
    Usage:
        class Invoice(db.Model, SoftDeleteMixin):
            ...
    """
    deleted_at = Column(DateTime, nullable=True)
    deleted_by = Column(String(64), nullable=True)
    delete_reason = Column(String(255), nullable=True)
    
    def soft_delete(self, reason=None, actor=None):
        """
        Soft delete this record by marking it as deleted.
        
        Args:
            reason: Optional reason for deletion
            actor: Username of person deleting (defaults to current_user if in request context)
        """
        if self.deleted_at:
            return
        
        self.deleted_at = datetime.utcnow()
        
        # Safely get actor - works in CLI, tests, background jobs, and request context
        if actor:
            self.deleted_by = actor
        else:
            try:
                # Try to get current_user (only works in request context)
                if hasattr(current_user, 'username'):
                    self.deleted_by = current_user.username
                else:
                    self.deleted_by = 'system'
            except RuntimeError:
                # current_user accessed outside request context (CLI/tests/background jobs)
                self.deleted_by = 'system'
        
        self.delete_reason = reason
    
    def restore(self):
        """Restore a soft-deleted record"""
        self.deleted_at = None
        self.deleted_by = None
        self.delete_reason = None
    
    @property
    def is_deleted(self):
        """Check if this record is soft-deleted"""
        return self.deleted_at is not None


class ActivatableMixin:
    """
    Mixin for entities that can be disabled/deactivated (Users, Customers).
    Allows keeping records for audit/history without allowing active use.
    
    Usage:
        class User(UserMixin, db.Model, ActivatableMixin):
            ...
    """
    is_active = Column(Boolean, nullable=False, default=True, server_default='true')
    disabled_at = Column(DateTime, nullable=True)
    disabled_reason = Column(String(255), nullable=True)
    
    def disable(self, reason=None):
        """
        Disable/deactivate this record.
        
        Args:
            reason: Optional reason for disabling
        """
        self.is_active = False
        self.disabled_at = datetime.utcnow()
        self.disabled_reason = reason
    
    def enable(self):
        """Re-enable/reactivate this record"""
        self.is_active = True
        self.disabled_at = None
        self.disabled_reason = None
