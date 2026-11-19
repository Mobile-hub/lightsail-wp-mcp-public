<?php
/**
 * Every bridge operation is both a route of wpmcp/v1 and, on WordPress 6.9 and later, an
 * ability of the Abilities API (wpmcp/get-content, wpmcp/update-draft, ...). Each file
 * defines its operations once (wpmcp_read_operations() and the like); this file registers
 * the routes and the abilities from those tables.
 *
 * The routes are what the wpmcp service calls, and they run the ability: every caller (the
 * service, WP-CLI's `wp ability run`, WordPress's AI client, an MCP Adapter server the site
 * owner sets up) goes through the same input schema, permission check and core lifecycle
 * hooks (wp_before_execute_ability, ...). The abilities are not public: neither
 * /wp-abilities/v1 nor the MCP Adapter's default server lists them, and the integration
 * user's REST allowlist (access.php) is unchanged. Budgets, scopes and the audit trail stay
 * in the wpmcp service; a caller that runs these abilities another way does without them.
 *
 * Without the Abilities API (WordPress before 6.9) the routes call the callbacks directly.
 */

defined( 'ABSPATH' ) || exit;

const WPMCP_BRIDGE_ABILITY_CATEGORY = 'wpmcp';

/** Every operation of the bridge, in the shape the *_operations() functions return. */
function wpmcp_operations(): array {
	return array_merge( wpmcp_read_operations(), wpmcp_write_operations(), wpmcp_live_operations(), wpmcp_menu_operations() );
}

/** Ability annotations; the wpmcp service keeps its own (tools/), which match these. */
function wpmcp_annotations( bool $readonly, bool $destructive, bool $idempotent ): array {
	return array(
		'readonly'    => $readonly,
		'destructive' => $destructive,
		'idempotent'  => $idempotent,
	);
}

// ---------------------------------------------------------------------------- routes

add_action( 'rest_api_init', 'wpmcp_register_routes' );

function wpmcp_register_routes(): void {
	foreach ( wpmcp_operations() as $op ) {
		register_rest_route(
			WPMCP_BRIDGE_NS,
			$op['route'],
			array(
				'methods'             => $op['methods'],
				'callback'            => static fn ( WP_REST_Request $request ) => wpmcp_dispatch( $op, $request ),
				'permission_callback' => $op['permission_callback'],
				'args'                => $op['args'] ?? array(),
			)
		);
	}
}

/** Runs an operation for a REST request: through its ability when there is one. */
function wpmcp_dispatch( array $op, WP_REST_Request $request ) {
	$ability = ! empty( $op['ability'] ) && function_exists( 'wp_get_ability' ) ? wp_get_ability( $op['ability'] ) : null;
	if ( ! $ability ) {
		return call_user_func( $op['callback'], $request );
	}
	$result = $ability->execute( empty( $op['args'] ) ? null : wpmcp_ability_input( $op, $request ) );
	if ( is_wp_error( $result ) ) {
		// The ability's own errors carry no HTTP status (REST would answer 500).
		$status = array(
			'ability_invalid_input'       => 400,
			'ability_invalid_permissions' => 403,
		);
		$code = $result->get_error_code();
		if ( isset( $status[ $code ] ) && empty( $result->get_error_data( $code )['status'] ) ) {
			$result->add_data( array( 'status' => $status[ $code ] ), $code );
		}
	}
	return $result;
}

/** The ability input of a REST request: the operation's arguments that it has. */
function wpmcp_ability_input( array $op, WP_REST_Request $request ): array {
	$input = array();
	foreach ( array_keys( $op['args'] ) as $name ) {
		if ( $request->has_param( $name ) ) {
			$input[ $name ] = $request->get_param( $name );
		}
	}
	return $input;
}

/**
 * The request an operation's callback and permission check expect, built from ability
 * input. Defaults are applied as REST applies them, so `has_param()` answers alike.
 */
function wpmcp_ability_request( array $op, $input ): WP_REST_Request {
	$method   = strtoupper( trim( explode( ',', (string) $op['methods'] )[0] ) );
	$request  = new WP_REST_Request( $method, '/' . WPMCP_BRIDGE_NS . $op['route'] );
	$defaults = array();
	foreach ( $op['args'] ?? array() as $name => $arg ) {
		if ( array_key_exists( 'default', $arg ) ) {
			$defaults[ $name ] = $arg['default'];
		}
	}
	$request->set_default_params( $defaults );
	foreach ( is_array( $input ) ? $input : array() as $name => $value ) {
		$request->set_param( $name, $value );
	}
	return $request;
}

// ------------------------------------------------------------------------- abilities

/** JSON Schema of an ability's input, from the REST arguments of its route. */
function wpmcp_input_schema( array $args ): array {
	$properties = array();
	$required   = array();
	foreach ( $args as $name => $arg ) {
		if ( true === ( $arg['required'] ?? false ) ) {
			$required[] = $name;
		}
		unset( $arg['required'] );
		$properties[ $name ] = $arg;
	}
	$schema = array(
		'type'                 => 'object',
		'properties'           => $properties,
		'additionalProperties' => false,
	);
	if ( $required ) {
		$schema['required'] = $required;
	}
	return $schema;
}

add_action( 'wp_abilities_api_categories_init', 'wpmcp_register_ability_category' );

function wpmcp_register_ability_category(): void {
	wp_register_ability_category(
		WPMCP_BRIDGE_ABILITY_CATEGORY,
		array(
			'label'       => 'wpmcp',
			'description' => 'Posts, pages, translations, SEO fields, images and menus, with the checks of the wpmcp bridge.',
		)
	);
}

add_action( 'wp_abilities_api_init', 'wpmcp_register_abilities' );

function wpmcp_register_abilities(): void {
	foreach ( wpmcp_operations() as $op ) {
		if ( empty( $op['ability'] ) ) {
			continue;
		}
		$args = array(
			'label'               => $op['label'],
			'description'         => $op['description'],
			'category'            => WPMCP_BRIDGE_ABILITY_CATEGORY,
			'output_schema'       => array( 'type' => 'object' ),
			'execute_callback'    => static fn ( $input = null ) => call_user_func( $op['callback'], wpmcp_ability_request( $op, $input ) ),
			'permission_callback' => static fn ( $input = null ) => call_user_func( $op['permission_callback'], wpmcp_ability_request( $op, $input ) ),
			'meta'                => array(
				'annotations'  => $op['annotations'],
				'show_in_rest' => false,
				'public'       => false,
			),
		);
		if ( ! empty( $op['args'] ) ) {
			$args['input_schema'] = wpmcp_input_schema( $op['args'] );
		}
		wp_register_ability( $op['ability'], $args );
	}
}
