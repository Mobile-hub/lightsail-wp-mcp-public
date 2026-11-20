<?php
/**
 * Plugin Name: wpmcp bridge
 * Description: Least-privilege access for the wpmcp integration user and the wpmcp/v1 REST routes it uses. Managed from the lightsail-wp-mcp repository; do not edit on the server.
 * Version: 0.11.0
 *
 * Must-use plugins are loaded from the top level of mu-plugins only: this file loads the rest.
 */

defined( 'ABSPATH' ) || exit;

define( 'WPMCP_BRIDGE_VERSION', '0.11.0' );

require_once __DIR__ . '/wpmcp-bridge/access.php';
require_once __DIR__ . '/wpmcp-bridge/helpers.php';
require_once __DIR__ . '/wpmcp-bridge/seo.php';
require_once __DIR__ . '/wpmcp-bridge/rest-read.php';
require_once __DIR__ . '/wpmcp-bridge/rest-write.php';
require_once __DIR__ . '/wpmcp-bridge/rest-live.php';
require_once __DIR__ . '/wpmcp-bridge/rest-menus.php';
require_once __DIR__ . '/wpmcp-bridge/rest-bulk.php';
require_once __DIR__ . '/wpmcp-bridge/abilities.php';
require_once __DIR__ . '/wpmcp-bridge/site-abilities.php';
