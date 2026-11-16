<?php
/**
 * Who the integration user is and what it may do.
 *
 * The wpmcp service authenticates as one dedicated WordPress user with an Application
 * Password. This file makes that credential as narrow as WordPress allows:
 *
 * - its capabilities come from a fixed list here, never from its role (other plugins add
 *   capabilities to roles; All in One SEO grants its own to anyone with edit_posts);
 * - only this user may have Application Passwords, and they work only from loopback;
 * - it cannot log in interactively or reset its password;
 * - over REST it reaches only the routes listed here (not backups, SEO admin, users,
 *   settings, plugins, ...).
 *
 * Configuration, in wp-config.php (optional):
 *   define( 'WPMCP_BRIDGE_USER_LOGIN', 'wpmcp' );
 *   define( 'WPMCP_BRIDGE_ALLOWED_IPS', '127.0.0.1,::1' );
 */

defined( 'ABSPATH' ) || exit;

/** Login name of the integration user. */
function wpmcp_user_login(): string {
	return defined( 'WPMCP_BRIDGE_USER_LOGIN' ) ? (string) WPMCP_BRIDGE_USER_LOGIN : 'wpmcp';
}

/** ID of the integration user, 0 when it does not exist. */
function wpmcp_user_id(): int {
	static $id = null;
	if ( null === $id ) {
		$user = get_user_by( 'login', wpmcp_user_login() );
		$id   = $user ? (int) $user->ID : 0;
	}
	return $id;
}

function wpmcp_is_integration_user( $user_id ): bool {
	$integration = wpmcp_user_id();
	return $integration > 0 && (int) $user_id === $integration;
}

/** Client addresses from which the Application Password is accepted. */
function wpmcp_allowed_ips(): array {
	$raw = defined( 'WPMCP_BRIDGE_ALLOWED_IPS' ) ? (string) WPMCP_BRIDGE_ALLOWED_IPS : '127.0.0.1,::1';
	return array_values( array_filter( array_map( 'trim', explode( ',', $raw ) ) ) );
}

/**
 * Every capability the integration user has. Content and media editing, categories;
 * nothing that changes users, plugins, themes, options or comments, and no unfiltered_html
 * (so everything it saves goes through kses). Menus (edit_theme_options) come in a later
 * release, behind their own routes.
 */
function wpmcp_capabilities(): array {
	return array(
		'read',
		'upload_files',
		'manage_categories',
		'edit_posts',
		'edit_others_posts',
		'edit_published_posts',
		'edit_private_posts',
		'publish_posts',
		'read_private_posts',
		'delete_posts',
		'delete_others_posts',
		'delete_published_posts',
		'delete_private_posts',
		'edit_pages',
		'edit_others_pages',
		'edit_published_pages',
		'edit_private_pages',
		'publish_pages',
		'read_private_pages',
		'delete_pages',
		'delete_others_pages',
		'delete_published_pages',
		'delete_private_pages',
	);
}

// Capabilities: the fixed list replaces whatever the role (and other plugins) granted.
add_filter(
	'user_has_cap',
	static function ( $allcaps, $caps, $args, $user ) {
		if ( ! $user instanceof WP_User || ! wpmcp_is_integration_user( $user->ID ) ) {
			return $allcaps;
		}
		return array_fill_keys( wpmcp_capabilities(), true );
	},
	PHP_INT_MAX,
	4
);

// Application Passwords: only the integration user can have or use them. Anyone else's
// would be a full credential that skips the login form and its second factor.
add_filter(
	'wp_is_application_passwords_available_for_user',
	static function ( $available, $user ) {
		return $available && $user instanceof WP_User && wpmcp_is_integration_user( $user->ID );
	},
	PHP_INT_MAX,
	2
);

// ... and only from loopback: the wpmcp service runs on this machine.
add_action(
	'wp_authenticate_application_password_errors',
	static function ( $error, $user ) {
		$remote = isset( $_SERVER['REMOTE_ADDR'] ) ? (string) $_SERVER['REMOTE_ADDR'] : '';
		if ( ! in_array( $remote, wpmcp_allowed_ips(), true ) ) {
			$error->add( 'wpmcp_remote_address', __( 'Application passwords are not accepted from this address.' ) );
		}
		if ( ! $user instanceof WP_User || ! wpmcp_is_integration_user( $user->ID ) ) {
			$error->add( 'wpmcp_user', __( 'Application passwords are not available for this user.' ) );
		}
	},
	10,
	2
);

// No interactive login (wp-login.php and anything else that calls wp_authenticate).
add_filter(
	'authenticate',
	static function ( $user ) {
		if ( $user instanceof WP_User && wpmcp_is_integration_user( $user->ID ) ) {
			return new WP_Error( 'wpmcp_no_login', __( 'This account cannot log in.' ) );
		}
		return $user;
	},
	PHP_INT_MAX
);

add_filter(
	'allow_password_reset',
	static function ( $allow, $user_id ) {
		return wpmcp_is_integration_user( $user_id ) ? false : $allow;
	},
	PHP_INT_MAX,
	2
);

/** REST routes the integration user may reach (regular expressions on the route). */
function wpmcp_allowed_routes(): array {
	return array(
		'#^/wpmcp/v1(/|$)#',
	);
}

function wpmcp_route_allowed( string $route ): bool {
	foreach ( wpmcp_allowed_routes() as $pattern ) {
		if ( preg_match( $pattern, $route ) ) {
			return true;
		}
	}
	return false;
}

// REST allowlist: runs before routing, so a denied route never reaches its callback.
add_filter(
	'rest_pre_dispatch',
	static function ( $result, $server, $request ) {
		if ( wpmcp_is_integration_user( get_current_user_id() ) && ! wpmcp_route_allowed( $request->get_route() ) ) {
			return new WP_Error( 'wpmcp_route_denied', __( 'This route is not available to the integration user.' ), array( 'status' => 403 ) );
		}
		return $result;
	},
	0,
	3
);
