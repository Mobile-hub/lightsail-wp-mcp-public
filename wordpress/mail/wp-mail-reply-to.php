<?php
/**
 * Plugin Name: Mail Reply-To
 * Description: Adds a Reply-To header (WP_MAIL_REPLY_TO, set with the SMTP settings by bin/mail-credentials) to mail that has none, so answers to a no-reply sender reach a real inbox. Managed from the lightsail-wp-mcp repository.
 * Version: 1.0.0
 */

defined( 'ABSPATH' ) || exit;

add_filter(
	'wp_mail',
	static function ( $args ) {
		if ( ! defined( 'WP_MAIL_REPLY_TO' ) || ! is_email( (string) WP_MAIL_REPLY_TO ) ) {
			return $args;
		}
		$headers = $args['headers'] ?? array();
		$lines   = is_array( $headers ) ? $headers : explode( "\n", str_replace( "\r\n", "\n", (string) $headers ) );
		foreach ( $lines as $line ) {
			if ( 0 === stripos( ltrim( (string) $line ), 'reply-to:' ) ) {
				return $args;
			}
		}
		$lines[]         = 'Reply-To: ' . WP_MAIL_REPLY_TO;
		$args['headers'] = $lines;
		return $args;
	}
);
