<?php
/**
 * Plugin Name: wpmcp dev site
 * Description: Development stack only (dev/compose.yml), never deployed: what the default block theme lacks and the integration tests need.
 */

defined( 'ABSPATH' ) || exit;

// A widget area, like a classic theme's footer: dev/provision.php puts one Navigation Menu
// widget per language in it (see wpmcp_widget_menus() in the bridge).
add_action(
	'widgets_init',
	static function () {
		register_sidebar( array( 'id' => 'wpmcp-dev-footer', 'name' => 'Dev footer' ) );
	}
);

// The site's address is the host's published port (http://127.0.0.1:8081); inside the
// container Apache listens on port 80. A visitor's request WordPress makes to its own
// address (SEOPress's content analysis loads the page) is sent there, as it reaches the
// site in production. GET only, so WP-Cron's spawn (a POST) still does not run.
add_filter(
	'pre_http_request',
	static function ( $pre, $args, $url ) {
		$home = untrailingslashit( home_url() );
		$port = wp_parse_url( $home, PHP_URL_PORT );
		if ( false !== $pre || ! $port || 'GET' !== ( $args['method'] ?? 'GET' ) || 0 !== strpos( $url, $home . '/' ) ) {
			return $pre;
		}
		$args['headers']         = is_array( $args['headers'] ?? null ) ? $args['headers'] : array();
		$args['headers']['Host'] = wp_parse_url( $home, PHP_URL_HOST ) . ':' . $port;
		return wp_remote_request( 'http://127.0.0.1' . substr( $url, strlen( $home ) ), $args );
	},
	10,
	3
);
