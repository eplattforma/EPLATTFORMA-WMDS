#!/usr/bin/env python3
"""
Quick admin password reset utility
Run this script to set a new password for the administrator account
"""

import os
from werkzeug.security import generate_password_hash
from app import app, db
from models import User

def reset_admin_password():
    """Reset the administrator password to 'admin123'"""
    with app.app_context():
        # Find the administrator user
        admin_user = User.query.filter_by(username='administrator').first()
        
        if not admin_user:
            print("Administrator user not found!")
            return False
        
        # Set new password
        new_password = 'admin123'
        admin_user.password = generate_password_hash(new_password)
        
        try:
            db.session.commit()
            print(f"✅ Administrator password reset successfully!")
            print(f"Username: administrator")
            print(f"Password: {new_password}")
            print(f"You can now log in with these credentials to access batch editing.")
            return True
        except Exception as e:
            db.session.rollback()
            print(f"❌ Error resetting password: {str(e)}")
            return False

if __name__ == '__main__':
    reset_admin_password()