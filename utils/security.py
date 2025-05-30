"""
ANSV Bot - Security Hardening Module
Provides additional security measures for public deployment
"""

import re
import time
import logging
import hashlib
import secrets
from typing import Dict, Optional, List
from datetime import datetime, timedelta
from functools import wraps
from flask import request, session, abort, current_app, g, redirect, url_for
import ipaddress

logger = logging.getLogger(__name__)

class SecurityConfig:
    """Security configuration constants"""
    
    # Rate limiting
    MAX_LOGIN_ATTEMPTS_PER_IP = 10  # Per hour
    MAX_REQUESTS_PER_IP = 1000      # Per hour
    LOCKOUT_DURATION_HOURS = 1
    
    # Password policies
    MIN_PASSWORD_LENGTH = 12
    REQUIRE_UPPERCASE = True
    REQUIRE_LOWERCASE = True
    REQUIRE_NUMBERS = True
    REQUIRE_SPECIAL_CHARS = True
    FORBIDDEN_PASSWORDS = [
        'password', 'admin', 'administrator', '123456', 'qwerty',
        'password123', 'admin123', 'letmein', 'welcome'
    ]
    
    # Username policies
    MIN_USERNAME_LENGTH = 3
    MAX_USERNAME_LENGTH = 30
    ALLOWED_USERNAME_CHARS = re.compile(r'^[a-zA-Z0-9_-]+$')
    FORBIDDEN_USERNAMES = [
        'admin', 'administrator', 'root', 'system', 'bot', 'test',
        'api', 'www', 'ftp', 'mail', 'email', 'support', 'help'
    ]
    
    # Session security
    SESSION_TIMEOUT_MINUTES = 30
    MAX_CONCURRENT_SESSIONS = 3
    SESSION_REGENERATE_INTERVAL = 15  # minutes

class RateLimiter:
    """IP-based rate limiting for authentication and requests"""
    
    def __init__(self):
        self.login_attempts: Dict[str, List[datetime]] = {}
        self.request_counts: Dict[str, List[datetime]] = {}
        self.lockouts: Dict[str, datetime] = {}
    
    def _get_client_ip(self) -> str:
        """Get real client IP, accounting for proxies"""
        # Check for X-Forwarded-For header (common with reverse proxies)
        if 'X-Forwarded-For' in request.headers:
            # Take the first IP in the chain
            forwarded_ips = request.headers['X-Forwarded-For'].split(',')
            client_ip = forwarded_ips[0].strip()
        elif 'X-Real-IP' in request.headers:
            client_ip = request.headers['X-Real-IP']
        else:
            client_ip = request.remote_addr
        
        # Validate IP format
        try:
            ipaddress.ip_address(client_ip)
            return client_ip
        except ValueError:
            logger.warning(f"Invalid IP format: {client_ip}, using remote_addr")
            return request.remote_addr or '127.0.0.1'
    
    def _cleanup_old_entries(self, entries: List[datetime], hours: int = 1) -> List[datetime]:
        """Remove entries older than specified hours"""
        cutoff = datetime.now() - timedelta(hours=hours)
        return [entry for entry in entries if entry > cutoff]
    
    def is_ip_locked(self, ip: str = None) -> bool:
        """Check if IP is currently locked out"""
        ip = ip or self._get_client_ip()
        
        if ip in self.lockouts:
            if datetime.now() < self.lockouts[ip]:
                return True
            else:
                # Lockout expired, remove it
                del self.lockouts[ip]
        
        return False
    
    def check_login_rate_limit(self, ip: str = None) -> bool:
        """Check if IP has exceeded login attempt rate limit"""
        ip = ip or self._get_client_ip()
        
        # Check if IP is locked out
        if self.is_ip_locked(ip):
            return False
        
        # Clean up old entries
        if ip in self.login_attempts:
            self.login_attempts[ip] = self._cleanup_old_entries(self.login_attempts[ip])
        else:
            self.login_attempts[ip] = []
        
        # Check rate limit
        if len(self.login_attempts[ip]) >= SecurityConfig.MAX_LOGIN_ATTEMPTS_PER_IP:
            # Lock out the IP
            self.lockouts[ip] = datetime.now() + timedelta(hours=SecurityConfig.LOCKOUT_DURATION_HOURS)
            logger.warning(f"IP {ip} locked out for excessive login attempts")
            return False
        
        return True
    
    def record_login_attempt(self, ip: str = None):
        """Record a login attempt for rate limiting"""
        ip = ip or self._get_client_ip()
        
        if ip not in self.login_attempts:
            self.login_attempts[ip] = []
        
        self.login_attempts[ip].append(datetime.now())
    
    def check_request_rate_limit(self, ip: str = None) -> bool:
        """Check if IP has exceeded general request rate limit"""
        ip = ip or self._get_client_ip()
        
        # Clean up old entries
        if ip in self.request_counts:
            self.request_counts[ip] = self._cleanup_old_entries(self.request_counts[ip])
        else:
            self.request_counts[ip] = []
        
        # Check rate limit
        if len(self.request_counts[ip]) >= SecurityConfig.MAX_REQUESTS_PER_IP:
            logger.warning(f"IP {ip} exceeded request rate limit")
            return False
        
        # Record this request
        self.request_counts[ip].append(datetime.now())
        return True

class PasswordValidator:
    """Validate passwords against security policies"""
    
    @staticmethod
    def validate_password(password: str, username: str = None) -> tuple[bool, List[str]]:
        """Validate password against security policies"""
        errors = []
        
        # Length check
        if len(password) < SecurityConfig.MIN_PASSWORD_LENGTH:
            errors.append(f"Password must be at least {SecurityConfig.MIN_PASSWORD_LENGTH} characters long")
        
        # Character requirements
        if SecurityConfig.REQUIRE_UPPERCASE and not re.search(r'[A-Z]', password):
            errors.append("Password must contain at least one uppercase letter")
        
        if SecurityConfig.REQUIRE_LOWERCASE and not re.search(r'[a-z]', password):
            errors.append("Password must contain at least one lowercase letter")
        
        if SecurityConfig.REQUIRE_NUMBERS and not re.search(r'\d', password):
            errors.append("Password must contain at least one number")
        
        if SecurityConfig.REQUIRE_SPECIAL_CHARS and not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
            errors.append("Password must contain at least one special character")
        
        # Forbidden passwords
        if password.lower() in SecurityConfig.FORBIDDEN_PASSWORDS:
            errors.append("This password is too common and not allowed")
        
        # Username similarity check
        if username and username.lower() in password.lower():
            errors.append("Password cannot contain your username")
        
        # Sequential characters check
        if re.search(r'(012|123|234|345|456|567|678|789|890|abc|bcd|cde|def)', password.lower()):
            errors.append("Password cannot contain sequential characters")
        
        # Repeated characters check
        if re.search(r'(.)\1{2,}', password):
            errors.append("Password cannot contain repeated characters")
        
        return len(errors) == 0, errors

class UsernameValidator:
    """Validate usernames against security policies"""
    
    @staticmethod
    def validate_username(username: str) -> tuple[bool, List[str]]:
        """Validate username against security policies"""
        errors = []
        
        # Length check
        if len(username) < SecurityConfig.MIN_USERNAME_LENGTH:
            errors.append(f"Username must be at least {SecurityConfig.MIN_USERNAME_LENGTH} characters long")
        
        if len(username) > SecurityConfig.MAX_USERNAME_LENGTH:
            errors.append(f"Username must be no more than {SecurityConfig.MAX_USERNAME_LENGTH} characters long")
        
        # Character check
        if not SecurityConfig.ALLOWED_USERNAME_CHARS.match(username):
            errors.append("Username can only contain letters, numbers, underscores, and hyphens")
        
        # Forbidden usernames
        if username.lower() in SecurityConfig.FORBIDDEN_USERNAMES:
            errors.append("This username is reserved and not allowed")
        
        # No leading/trailing special chars
        if username.startswith(('_', '-')) or username.endswith(('_', '-')):
            errors.append("Username cannot start or end with underscore or hyphen")
        
        # No consecutive special chars
        if re.search(r'[_-]{2,}', username):
            errors.append("Username cannot contain consecutive underscores or hyphens")
        
        return len(errors) == 0, errors

class CSRFProtection:
    """CSRF token generation and validation"""
    
    @staticmethod
    def generate_csrf_token() -> str:
        """Generate a CSRF token for the current session"""
        if 'csrf_token' not in session:
            session['csrf_token'] = secrets.token_hex(32)
        return session['csrf_token']
    
    @staticmethod
    def validate_csrf_token(token: str) -> bool:
        """Validate CSRF token against session"""
        session_token = session.get('csrf_token')
        if not session_token or not token:
            return False
        
        # Use constant-time comparison to prevent timing attacks
        return secrets.compare_digest(session_token, token)

class SessionSecurity:
    """Enhanced session security management"""
    
    @staticmethod
    def regenerate_session_id():
        """Regenerate session ID to prevent session fixation"""
        # Store important session data
        user_data = session.get('user_id')
        csrf_token = session.get('csrf_token')
        
        # Clear old session
        session.clear()
        
        # Restore important data
        if user_data:
            session['user_id'] = user_data
        if csrf_token:
            session['csrf_token'] = csrf_token
        
        # Mark regeneration time
        session['last_regenerated'] = datetime.now().isoformat()
    
    @staticmethod
    def should_regenerate_session() -> bool:
        """Check if session should be regenerated"""
        last_regen = session.get('last_regenerated')
        if not last_regen:
            return True
        
        last_regen_time = datetime.fromisoformat(last_regen)
        return datetime.now() - last_regen_time > timedelta(minutes=SecurityConfig.SESSION_REGENERATE_INTERVAL)
    
    @staticmethod
    def is_session_expired() -> bool:
        """Check if session has expired"""
        last_activity = session.get('last_activity')
        if not last_activity:
            return True
        
        last_activity_time = datetime.fromisoformat(last_activity)
        return datetime.now() - last_activity_time > timedelta(minutes=SecurityConfig.SESSION_TIMEOUT_MINUTES)
    
    @staticmethod
    def update_session_activity():
        """Update session last activity timestamp"""
        session['last_activity'] = datetime.now().isoformat()

# Global rate limiter instance
rate_limiter = RateLimiter()

def require_rate_limit(func):
    """Decorator to enforce rate limiting"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not rate_limiter.check_request_rate_limit():
            logger.warning(f"Rate limit exceeded for IP: {rate_limiter._get_client_ip()}")
            abort(429)  # Too Many Requests
        return func(*args, **kwargs)
    return wrapper

def require_csrf_token(func):
    """Decorator to require CSRF token validation for POST requests"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        if request.method == 'POST':
            token = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
            if not CSRFProtection.validate_csrf_token(token):
                logger.warning(f"CSRF token validation failed for IP: {rate_limiter._get_client_ip()}")
                abort(403)  # Forbidden
        return func(*args, **kwargs)
    return wrapper

def secure_headers(response):
    """Add security headers to responses"""
    # Prevent clickjacking
    response.headers['X-Frame-Options'] = 'DENY'
    
    # Prevent MIME type sniffing
    response.headers['X-Content-Type-Options'] = 'nosniff'
    
    # XSS protection
    response.headers['X-XSS-Protection'] = '1; mode=block'
    
    # Referrer policy
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    
    # Content Security Policy
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "font-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    
    # Strict Transport Security (HTTPS only)
    if request.is_secure:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    
    return response

def enforce_https():
    """Redirect HTTP to HTTPS in production"""
    if not request.is_secure and not current_app.debug:
        return redirect(request.url.replace('http://', 'https://'), code=301)

def session_security_middleware():
    """Middleware for session security checks"""
    # Skip security checks for static files
    if request.endpoint and request.endpoint.startswith('static'):
        return
    
    # Check if session has expired
    if SessionSecurity.is_session_expired():
        session.clear()
        if request.endpoint not in ['login', 'static']:
            return redirect(url_for('login'))
    
    # Regenerate session ID if needed
    if SessionSecurity.should_regenerate_session():
        SessionSecurity.regenerate_session_id()
    
    # Update session activity
    SessionSecurity.update_session_activity()