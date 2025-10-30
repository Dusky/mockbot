# ANSV Bot Overhaul Plan

## 🎯 Business Model

### Target Users
- Twitch streamers who want an AI chat bot

### Pricing Structure
- **FREE**: Full bot features (Markov text generation, unlimited channels learning, all settings)
- **PREMIUM ($2/mo)**: Everything in Free + Text-to-Speech (the ONLY premium feature)

### Key Principle
**1 Account = 1 Twitch Channel**
- Each streamer manages their own channel only
- ANSV bot (centralized) joins all user channels
- No multi-channel management per user

---

## 🏗️ Architecture Decisions

### User Model
- **Keep Roles**: super_admin (platform admin), streamer (all users)
- **Remove Roles**: admin, moderator, viewer (not needed)

### Channel Assignment
- **Approach**: Add `managed_channel` column directly to `users` table
- **Enforcement**: One channel per user account
- **Source**: Auto-detected from Twitch OAuth or manually entered

### Subscription System
- **Provider**: Stripe
- **Tier Storage**: `subscription_tier` column ('free' or 'premium')
- **Feature Gate**: Simple check for TTS access only

### Authentication
- **Primary**: Twitch OAuth (recommended flow)
- **Fallback**: Email/password signup
- **Auto-populate**: Channel name from OAuth

---

## 📊 Current Implementation Status

### ✅ Already Implemented
- [x] User management system with roles
- [x] Session management & audit logging
- [x] User-channel assignments table (needs refactoring)
- [x] Streamer redirect logic
- [x] Modern beta UI (dashboard, settings, stats)
- [x] Database schemas (messages.db, users.db)
- [x] Channel configs and bot controls

### ❌ Not Started
- [ ] Twitch OAuth integration
- [ ] Subscription/billing system
- [ ] Stripe integration
- [ ] Premium feature gating
- [ ] Public landing page
- [ ] Onboarding flow
- [ ] One-channel-per-user enforcement
- [ ] Simplified streamer-only UI

### 🔄 Needs Refactoring
- [ ] Remove unnecessary roles (admin, moderator, viewer)
- [ ] Simplify multi-channel to single-channel model
- [ ] Clean up complex permission system
- [ ] Update dashboard to show single channel only

---

## 🗺️ Implementation Roadmap

### Phase 1: Database Schema Updates ✅ COMPLETED
**Goal**: Add subscription and Twitch OAuth fields

- [x] Add to `users` table:
  - `twitch_user_id` VARCHAR(50) UNIQUE
  - `twitch_username` VARCHAR(50)
  - `avatar_url` TEXT
  - `managed_channel` VARCHAR(100)
  - `subscription_tier` VARCHAR(20) DEFAULT 'free'
  - `subscription_status` VARCHAR(20) DEFAULT 'inactive'
  - `stripe_customer_id` VARCHAR(100)
  - `onboarding_completed` BOOLEAN DEFAULT 0

- [x] Create `subscriptions` table:
  - id, user_id, stripe_subscription_id
  - status, current_period_start, current_period_end
  - cancel_at_period_end, created_at, updated_at

- [x] Create `payments` table:
  - id, user_id, amount, currency, status
  - stripe_payment_intent_id, description, created_at

- [x] Add `user_id` to `channel_configs` table

- [x] Create migration logic for existing databases

- [x] Add subscription helper methods:
  - `has_tts_access()` - Check if user has Premium
  - `get_subscription_status()` - Get subscription details
  - `update_subscription()` - Update subscription tier/status

**Files modified**:
- `utils/db_setup.py` (messages.db)
- `utils/user_db.py` (users.db)

---

### Phase 2: Simplify Role System ✅ COMPLETED
**Goal**: Remove unnecessary complexity

- [x] Update `user_db.py` role definitions:
  - Keep: super_admin, streamer
  - Remove: admin, moderator, viewer

- [x] Simplify permissions:
  - super_admin: ['*']
  - streamer: ['dashboard.view', 'own_channel.*', 'tts.own_channel']

- [x] Update permission checks in auth.py:
  - Simplified channel access checks
  - Use managed_channel field for streamers
  - Removed references to old roles

- [x] Update role constants (Roles class)

**Files modified**:
- `utils/user_db.py`
- `utils/auth.py`

---

### Phase 3: Twitch OAuth Integration ✅ COMPLETED
**Goal**: Allow streamers to sign up with Twitch

- [x] Add OAuth environment variables to `.env.example`:
  - TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET, TWITCH_REDIRECT_URI
  - Added registration instructions

- [x] Add OAuth routes to `webapp.py`:
  - `GET /auth/twitch` - Initiate OAuth flow with state parameter
  - `GET /auth/twitch/callback` - Handle OAuth callback

- [x] OAuth callback features:
  - Exchange code for access token
  - Fetch user data from Twitch API
  - Create new users automatically with streamer role
  - Update existing users on re-authentication
  - Set `managed_channel` to Twitch username
  - CSRF protection with state parameter

- [x] Update `login.html`:
  - Add prominent "Sign in with Twitch" button with Twitch branding
  - Add divider with "or continue with email"
  - Keep email/password as fallback
  - Style with Twitch purple colors (#9146FF)

**Files modified**:
- `.env.example` (OAuth credentials)
- `webapp.py` (OAuth routes + imports)
- `templates/login.html` (OAuth button + styling)

**Note**: Users must register a Twitch OAuth app at https://dev.twitch.tv/console/apps and add credentials to `.env` file

---

### Phase 4: Landing Page & Public UI (3-4 days)
**Goal**: Create public-facing pages for new users

- [ ] Create `templates/landing.html`:
  - Hero section with value prop
  - Features showcase
  - Pricing comparison (Free vs Premium)
  - Testimonials (optional)
  - CTA: "Sign in with Twitch"

- [ ] Update root route `/`:
  - Show landing page if not authenticated
  - Redirect to dashboard if authenticated

- [ ] Create `templates/pricing.html`:
  - Feature comparison table
  - Why TTS is premium explanation

- [ ] Design Twitch-inspired branding:
  - Primary: #9146FF (Twitch purple)
  - Secondary: #772CE8 (darker purple)
  - Accent: #00F5FF (cyan)

- [ ] Public navigation (no auth required)

**Files to create/modify**:
- `templates/landing.html` (new)
- `templates/pricing.html` (new)
- `templates/public_base.html` (new, for public pages)
- `static/css/landing.css` (new)
- `webapp.py` (update root route)

---

### Phase 5: Onboarding Flow (2-3 days)
**Goal**: Guide new users through setup

- [ ] Create onboarding wizard templates:
  - `templates/onboarding/welcome.html`
  - `templates/onboarding/confirm_channel.html`
  - `templates/onboarding/settings.html`
  - `templates/onboarding/premium_upsell.html`

- [ ] Add onboarding routes:
  - `GET /onboarding` - Check completion status
  - `POST /onboarding/channel` - Save channel
  - `POST /onboarding/settings` - Save initial settings
  - `POST /onboarding/complete` - Mark complete

- [ ] Track completion:
  - `onboarding_completed` flag in users table
  - Redirect to onboarding if not completed

- [ ] Auto-create channel config on completion

**Files to create/modify**:
- `templates/onboarding/` (new directory)
- `webapp.py` (onboarding routes)
- `utils/user_db.py` (onboarding helpers)

---

### Phase 6: Stripe Integration ✅ COMPLETED
**Goal**: Enable premium subscriptions

- [x] Set up Stripe account:
  - Create product: "ANSV Premium"
  - Create price: $2/month recurring
  - Get API keys (test & live)

- [x] Add Stripe SDK:
  - Added `stripe>=7.0.0` to requirements.txt

- [x] Create subscription endpoints:
  - `POST /checkout/create` - Create checkout session
  - `GET /checkout/success` - Handle success redirect
  - `GET /checkout/cancel` - Handle cancel redirect
  - `POST /webhook/stripe` - Webhook handler

- [x] Implement webhook events:
  - `checkout.session.completed` → Activate subscription
  - `invoice.payment_succeeded` → Renew subscription
  - `invoice.payment_failed` → Mark as past_due
  - `customer.subscription.deleted` → Cancel subscription
  - `customer.subscription.updated` → Handle cancellation scheduling

- [x] Create billing page:
  - `templates/beta/premium.html` with pricing and features
  - Shows current plan status
  - Upgrade button with Stripe Checkout (free users)
  - Manage subscription button with Customer Portal (premium users)
  - Feature comparison table

- [x] Add Stripe keys to environment:
  - Added to `.env.example`:
    - `STRIPE_SECRET_KEY`
    - `STRIPE_PUBLISHABLE_KEY`
    - `STRIPE_WEBHOOK_SECRET`
    - `STRIPE_PREMIUM_PRICE_ID`

**Files created/modified**:
- `webapp.py` (Stripe routes: /premium, /checkout/*, /billing/portal, /webhook/stripe)
- `utils/stripe_service.py` (StripeService class with all helper methods)
- `templates/beta/premium.html` (premium subscription page)
- `templates/checkout_success.html` (success page)
- `templates/checkout_cancel.html` (cancel page)
- `.env.example` (Stripe configuration)
- `requirements.txt` (stripe package)

---

### Phase 7: Premium Feature Gating ✅ COMPLETED
**Goal**: Lock TTS behind subscription

- [x] Add subscription check helper to User model:
  ```python
  def has_tts_access(self):
      return self.subscription_tier == 'premium' and \
             self.subscription_status == 'active'
  ```

- [x] Update TTS endpoints:
  - Check `current_user.has_tts_access()` before generation
  - Return error with upgrade prompt if false

- [x] Lock TTS UI for free users:
  - Show lock icon on TTS sections
  - Add upgrade CTAs
  - Disable TTS controls

- [x] Update channel settings page:
  - Free users: Show locked TTS section with upgrade button
  - Premium users: Show full TTS controls

- [x] Add subscription status to dashboard:
  - Show current plan (Free/Premium)
  - Show next billing date (Premium)
  - Prominent upgrade button (Free)

**Files modified**:
- `webapp.py` (TTS route checks, subscription status passed to templates)
- `utils/user_db.py` (has_tts_access helper method)
- `templates/beta/channel.html` (locked TTS toggle, Quick TTS panel with upgrade prompt)
- `templates/beta/dashboard.html` (subscription banner with plan status)
- `templates/beta/settings.html` (locked TTS toggles, voice settings, global TTS config)

---

### Phase 8: Simplified Streamer Dashboard ✅ COMPLETED
**Goal**: Single-channel focused UI

- [x] Update `redirect_streamers_to_channel()`:
  - Always redirect to their `managed_channel`
  - Simplify logic (no "first assigned channel")

- [x] Update dashboard (`templates/beta/dashboard.html`):
  - Show single channel card for streamers
  - Display channel stats with singular language
  - Show subscription status prominently
  - Super admins still see multi-channel view

- [x] Update navigation:
  - Navigation shows "My Channel" for streamers
  - Added Premium link for all users
  - Channels dropdown only for super admins
  - Analytics accessible to all users

- [x] Remove "Add Channel" functionality for streamers:
  - Hide UI button for streamers
  - Super admins retain this functionality
  - Channel auto-created on OAuth signup

- [x] Update settings page:
  - Settings for "My Channel" only (streamers)
  - Hide "Add Channel" button for streamers
  - Super admins see all channels
  - Updated title and subtitle for single-channel focus

**Files modified**:
- `webapp.py` (redirect logic, dashboard & settings routes)
- `templates/beta/dashboard.html` (single-channel UI)
- `templates/beta/settings.html` (single-channel settings)
- `templates/beta/base.html` (navigation updates)

---

### Phase 9: Testing & Polish (2-3 days)
**Goal**: Ensure everything works smoothly

- [ ] Complete user flow testing:
  - Sign up with Twitch OAuth
  - Complete onboarding
  - Configure bot settings
  - Try TTS (locked for free)
  - Upgrade to Premium
  - TTS now works
  - Cancel subscription
  - TTS locks again

- [ ] Stripe webhook testing:
  - Use Stripe CLI for local testing
  - Test successful payment
  - Test failed payment
  - Test subscription cancellation

- [ ] Edge cases:
  - User with no channel name
  - Expired subscription grace period
  - Payment method update
  - Account deletion (cascade deletes)

- [ ] Security audit:
  - OAuth token security
  - Stripe webhook signature verification
  - SQL injection prevention
  - XSS prevention
  - CSRF token checks

- [ ] Performance testing:
  - Database query optimization
  - Page load times
  - Bot startup time

- [ ] Email notifications (optional):
  - Welcome email
  - Payment success
  - Payment failed
  - Subscription cancelled

**Files to modify**:
- Various (bug fixes)
- Add email service (optional)

---

## 🎨 Design System

### Color Palette (Twitch-Inspired)
```css
--primary:   #9146FF;  /* Twitch purple */
--secondary: #772CE8;  /* Darker purple */
--accent:    #00F5FF;  /* Cyan */
--success:   #00CC66;  /* Green */
--warning:   #FFB800;  /* Gold - premium badge */
--danger:    #FF4757;  /* Red */

--bg-light:  #FAFAFA;
--bg-dark:   #0E0E10;
```

### Premium Badge
- Free: 🆓 Simple text
- Premium: ⭐ Gold star icon

### Feature Lock Icon
- 🔒 Lock icon with "Upgrade to Premium" text

---

## 📝 Environment Variables Needed

```bash
# Twitch OAuth
TWITCH_CLIENT_ID=your_client_id
TWITCH_CLIENT_SECRET=your_client_secret
TWITCH_REDIRECT_URI=http://localhost:5001/auth/twitch/callback

# Stripe
STRIPE_SECRET_KEY=sk_test_...
STRIPE_PUBLISHABLE_KEY=pk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PRICE_ID=price_...  # Premium subscription price ID

# Application
SECRET_KEY=your-secret-key-here
DATABASE_URL=sqlite:///messages.db
USERS_DATABASE_URL=sqlite:///users.db
```

---

## 🚦 Current Status

**Phase**: Core Platform Complete ✅ - Ready for Polish & Launch
**Next Action**: Landing Page, Onboarding, or Testing & Polish

### Completed Phases:
- Phase 1: Database Schema Updates ✅
- Phase 2: Role Simplification ✅
- Phase 3: Twitch OAuth Integration ✅
- Phase 6: Stripe Integration ✅
- Phase 7: Premium Feature Gating ✅
- Phase 8: Simplified Streamer Dashboard ✅

### Remaining Phases (Optional Polish):
- Phase 4: Landing Page & Public UI (nice-to-have marketing)
- Phase 5: Onboarding Flow (nice-to-have wizard)
- Phase 9: Testing & Polish (recommended before launch)

---

## 📌 Important Notes

### Migration Considerations
- No existing users to migrate (confirmed by user)
- Fresh start - can modify schema freely
- No data loss concerns

### Decisions Made
- ✅ Pricing: $2/month for Premium
- ✅ OAuth: Twitch required (email/password fallback)
- ✅ Feature gate: TTS only
- ✅ Branding: Twitch purple theme
- ✅ Channel model: One channel per user

### Open Questions
- [ ] Domain name for production?
- [ ] Logo design needed?
- [ ] Email service provider (SendGrid, Mailgun)?
- [ ] Annual pricing option ($20/year)?

---

## 🔗 Key Files Reference

### Core Application
- `webapp.py` - Main Flask app (2,882 lines)
- `ansv.py` - Bot entry point (186 lines)

### User Management
- `utils/user_db.py` - User database & auth (1,141 lines)
- `utils/auth.py` - Auth decorators (378 lines)
- `utils/security.py` - Security helpers (376 lines)

### Bot Logic
- `utils/bot.py` - TwitchIO bot (1,962 lines)
- `utils/markov_handler.py` - Text generation (815 lines)
- `utils/tts.py` - Text-to-speech (693 lines)

### Database
- `utils/db_setup.py` - Messages DB schema (221 lines)
- `messages.db` - Bot data (messages, channels, TTS logs)
- `users.db` - User accounts, roles, sessions

### Templates (Beta UI)
- `templates/beta/base.html` - Base layout (137 lines)
- `templates/beta/dashboard.html` - Main dashboard (223 lines)
- `templates/beta/channel.html` - Channel management (484 lines)
- `templates/beta/settings.html` - Settings page (546 lines)
- `templates/login.html` - Login page (420 lines)

---

## ✅ Next Steps

**Ready to start Phase 1!**

Choose your approach:
1. Database schema updates first (recommended)
2. Landing page + OAuth first (visual progress)
3. Full sequential implementation

Once decided, we'll begin implementation with detailed code changes.

---

*Last updated: 2025-10-29*
*Status: Planning Complete - Ready for Implementation*
