# ANSV Bot Security Guide

## 🔒 Security Features Implemented

This system has been hardened for **public deployment** with comprehensive security measures:

### Authentication Security
- **bcrypt password hashing** with salt (replacing legacy SHA256)
- **Account lockout** after 5 failed attempts (30 minutes)
- **Strong password requirements** (minimum 12 characters, mixed case, numbers, special chars)
- **Session security** with automatic regeneration and timeout (30 minutes)
- **Rate limiting** for login attempts (10 per hour per IP)

### Input Validation & Sanitization
- **Username validation** (3-30 characters, alphanumeric + underscore/hyphen only)
- **Forbidden username list** (admin, root, system, etc.)
- **Password complexity validation** (prevents common passwords, sequential chars, username similarity)
- **SQL injection protection** via parameterized queries
- **XSS protection** via input sanitization

### Session & Request Security
- **CSRF protection** on all forms with secure tokens
- **Session fixation prevention** via ID regeneration
- **HTTP rate limiting** (1000 requests per hour per IP)
- **Session timeout** and automatic cleanup
- **Secure cookie settings** (HTTPOnly, Secure, SameSite)

### HTTP Security Headers
- **X-Frame-Options: DENY** (clickjacking protection)
- **X-Content-Type-Options: nosniff** (MIME sniffing protection) 
- **X-XSS-Protection: 1; mode=block** (XSS filtering)
- **Content-Security-Policy** (script injection protection)
- **Strict-Transport-Security** (HTTPS enforcement)
- **Referrer-Policy: strict-origin-when-cross-origin**

### Infrastructure Security
- **HTTPS enforcement** in production (automatic HTTP→HTTPS redirect)
- **Environment-based secrets** (Flask secret key via environment variable)
- **Audit logging** for all administrative actions
- **IP-based rate limiting** with automatic lockouts

## 🚀 Production Deployment Security

### Required Environment Variables
```bash
export FLASK_SECRET_KEY="your-secure-random-key-here"
export FLASK_ENV="production"
```

### HTTPS Setup
The application automatically enforces HTTPS in production mode. Ensure your reverse proxy (nginx/Apache) or cloud provider handles SSL termination.

### Firewall Configuration
- Only expose ports 80 (HTTP redirect) and 443 (HTTPS)
- Consider restricting admin access by IP if possible
- Use fail2ban or similar for additional DDoS protection

### Database Security
- **Separate databases**: `users.db` (authentication) and `messages.db` (chat data)
- **File permissions**: Ensure database files are not web-accessible
- **Regular backups**: Implement automated backup strategy

## 🛡️ Security Validation

Run the security test to validate all protections:
```bash
python3 test_security.py
```

### Password Requirements
- Minimum 12 characters
- At least 1 uppercase letter
- At least 1 lowercase letter  
- At least 1 number
- At least 1 special character (!@#$%^&*())
- No username similarity
- No sequential characters (abc, 123)
- No repeated characters (aaa, 111)
- Not in forbidden password list

### Username Requirements
- 3-30 characters length
- Letters, numbers, underscore, hyphen only
- Cannot start/end with underscore or hyphen
- No consecutive special characters
- Not in reserved username list

## ⚡ Rate Limiting

### Login Attempts
- **10 attempts per hour per IP**
- **30-minute lockout** after limit exceeded
- **5 failed attempts per user account** triggers 30-minute lockout

### General Requests
- **1000 requests per hour per IP**
- **429 Too Many Requests** response when exceeded
- Static files excluded from rate limiting

## 🔧 Emergency Admin Creation

If you lose admin access, use the secure emergency tool:
```bash
python3 create_admin.py
```

This tool includes:
- Multiple confirmation prompts
- Enhanced password validation
- Secure password generation option
- Detection of existing admin accounts

## 📋 Security Checklist for Public Deployment

- [ ] Set strong `FLASK_SECRET_KEY` environment variable
- [ ] Enable HTTPS with valid SSL certificate
- [ ] Configure firewall to allow only ports 80/443
- [ ] Set up automated database backups
- [ ] Monitor logs for suspicious activity
- [ ] Regularly update dependencies
- [ ] Test all security features before going live
- [ ] Set up fail2ban or DDoS protection
- [ ] Consider IP whitelisting for admin access
- [ ] Document incident response procedures

## 🚨 Security Monitoring

The system logs all security events including:
- Failed login attempts with IP addresses
- Account lockouts and rate limit violations
- Administrative actions and user management
- CSRF token validation failures
- Suspicious input validation failures

Monitor these logs regularly and set up alerts for unusual patterns.

## 📞 Security Contact

For security issues or vulnerabilities, please create an issue in the repository with the `security` label.

---

**Last Updated**: $(date +%Y-%m-%d)
**Security Audit Status**: ✅ Comprehensive security measures implemented and tested