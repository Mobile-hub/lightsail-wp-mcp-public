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
