"""
Stripe payment integration service for ANSV Bot Premium subscriptions.
"""

import os
import stripe
from datetime import datetime
from typing import Optional, Dict, Any
import logging

# Initialize logger
logger = logging.getLogger(__name__)

# Initialize Stripe with secret key
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')


class StripeService:
    """Handle Stripe payment operations for Premium subscriptions."""

    def __init__(self):
        self.secret_key = os.getenv('STRIPE_SECRET_KEY')
        self.publishable_key = os.getenv('STRIPE_PUBLISHABLE_KEY')
        self.webhook_secret = os.getenv('STRIPE_WEBHOOK_SECRET')
        self.premium_price_id = os.getenv('STRIPE_PREMIUM_PRICE_ID')

        if not self.secret_key:
            logger.warning("STRIPE_SECRET_KEY not set - Stripe integration disabled")

        stripe.api_key = self.secret_key

    def create_checkout_session(self, user_id: int, user_email: str,
                               success_url: str, cancel_url: str) -> Optional[Dict[str, Any]]:
        """
        Create a Stripe checkout session for Premium subscription.

        Args:
            user_id: Internal user ID
            user_email: User's email address
            success_url: URL to redirect after successful payment
            cancel_url: URL to redirect if payment is cancelled

        Returns:
            Dictionary with checkout session details or None if failed
        """
        try:
            session = stripe.checkout.Session.create(
                customer_email=user_email,
                payment_method_types=['card'],
                line_items=[{
                    'price': self.premium_price_id,
                    'quantity': 1,
                }],
                mode='subscription',
                success_url=success_url,
                cancel_url=cancel_url,
                client_reference_id=str(user_id),
                metadata={
                    'user_id': str(user_id),
                },
                subscription_data={
                    'metadata': {
                        'user_id': str(user_id),
                    }
                }
            )

            logger.info(f"Created checkout session for user {user_id}: {session.id}")

            return {
                'session_id': session.id,
                'url': session.url,
                'success': True
            }

        except stripe.error.StripeError as e:
            logger.error(f"Stripe error creating checkout session: {e}")
            return {
                'error': str(e),
                'success': False
            }
        except Exception as e:
            logger.error(f"Error creating checkout session: {e}")
            return {
                'error': str(e),
                'success': False
            }

    def create_customer_portal_session(self, customer_id: str,
                                       return_url: str) -> Optional[Dict[str, Any]]:
        """
        Create a Stripe customer portal session for subscription management.

        Args:
            customer_id: Stripe customer ID
            return_url: URL to return to after portal session

        Returns:
            Dictionary with portal session URL or None if failed
        """
        try:
            session = stripe.billing_portal.Session.create(
                customer=customer_id,
                return_url=return_url,
            )

            logger.info(f"Created portal session for customer {customer_id}")

            return {
                'url': session.url,
                'success': True
            }

        except stripe.error.StripeError as e:
            logger.error(f"Stripe error creating portal session: {e}")
            return {
                'error': str(e),
                'success': False
            }
        except Exception as e:
            logger.error(f"Error creating portal session: {e}")
            return {
                'error': str(e),
                'success': False
            }

    def get_subscription(self, subscription_id: str) -> Optional[Dict[str, Any]]:
        """
        Get subscription details from Stripe.

        Args:
            subscription_id: Stripe subscription ID

        Returns:
            Dictionary with subscription details or None if failed
        """
        try:
            subscription = stripe.Subscription.retrieve(subscription_id)

            return {
                'id': subscription.id,
                'status': subscription.status,
                'current_period_end': subscription.current_period_end,
                'current_period_start': subscription.current_period_start,
                'customer': subscription.customer,
                'cancel_at_period_end': subscription.cancel_at_period_end,
                'success': True
            }

        except stripe.error.StripeError as e:
            logger.error(f"Stripe error getting subscription: {e}")
            return {
                'error': str(e),
                'success': False
            }
        except Exception as e:
            logger.error(f"Error getting subscription: {e}")
            return {
                'error': str(e),
                'success': False
            }

    def cancel_subscription(self, subscription_id: str,
                          immediately: bool = False) -> Optional[Dict[str, Any]]:
        """
        Cancel a subscription.

        Args:
            subscription_id: Stripe subscription ID
            immediately: If True, cancel immediately. If False, cancel at period end.

        Returns:
            Dictionary with cancellation result
        """
        try:
            if immediately:
                subscription = stripe.Subscription.delete(subscription_id)
            else:
                subscription = stripe.Subscription.modify(
                    subscription_id,
                    cancel_at_period_end=True
                )

            logger.info(f"Cancelled subscription {subscription_id} (immediately={immediately})")

            return {
                'id': subscription.id,
                'status': subscription.status,
                'success': True
            }

        except stripe.error.StripeError as e:
            logger.error(f"Stripe error cancelling subscription: {e}")
            return {
                'error': str(e),
                'success': False
            }
        except Exception as e:
            logger.error(f"Error cancelling subscription: {e}")
            return {
                'error': str(e),
                'success': False
            }

    def construct_webhook_event(self, payload: bytes, sig_header: str) -> Optional[Any]:
        """
        Verify and construct webhook event from Stripe.

        Args:
            payload: Raw request body
            sig_header: Stripe signature header

        Returns:
            Stripe Event object or None if verification failed
        """
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, self.webhook_secret
            )
            return event
        except ValueError as e:
            logger.error(f"Invalid webhook payload: {e}")
            return None
        except stripe.error.SignatureVerificationError as e:
            logger.error(f"Invalid webhook signature: {e}")
            return None

    def get_customer(self, customer_id: str) -> Optional[Dict[str, Any]]:
        """
        Get customer details from Stripe.

        Args:
            customer_id: Stripe customer ID

        Returns:
            Dictionary with customer details or None if failed
        """
        try:
            customer = stripe.Customer.retrieve(customer_id)

            return {
                'id': customer.id,
                'email': customer.email,
                'name': customer.name,
                'success': True
            }

        except stripe.error.StripeError as e:
            logger.error(f"Stripe error getting customer: {e}")
            return {
                'error': str(e),
                'success': False
            }
        except Exception as e:
            logger.error(f"Error getting customer: {e}")
            return {
                'error': str(e),
                'success': False
            }


# Global instance
stripe_service = StripeService()
