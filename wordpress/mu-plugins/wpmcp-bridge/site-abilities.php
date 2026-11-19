<?php
/**
 * Abilities of WordPress core or other plugins that the site owner lets the wpmcp service
 * run, by exact name. Nothing is listed by default. The list:
 *
 *   define( 'WPMCP_BRIDGE_ABILITIES', 'core/get-user-info,example/get-forecast' );   // wp-config.php
 *   add_filter( 'wpmcp_bridge_abilities', fn ( $names ) => [ ...$names, 'x/y' ] );     // a site mu-plugin
 *
 * Each one runs as the integration user, so its fixed capabilities (access.php) still
 * decide: an ability that needs manage_options, for example, is refused. Unlike the
 * bridge's own operations, these may have no version check, no revision and no way back:
 * list only abilities whose effect is understood. Runs of abilities that are not read-only
 * go into the owner's hourly digest. The wpmcp service turns each into a tool from its
 * schema and annotations (tools/abilities.py).
 */

defined( 'ABSPATH' ) || exit;

const WPMCP_BRIDGE_SITE_ABILITIES_MAX = 20;

/** The names the owner listed: well formed, never the bridge's own, at most 20. */
function wpmcp_site_ability_names(): array {
	$listed = defined( 'WPMCP_BRIDGE_ABILITIES' ) ? explode( ',', (string) WPMCP_BRIDGE_ABILITIES ) : array();
	$listed = (array) apply_filters( 'wpmcp_bridge_abilities', array_map( 'trim', $listed ) );
	$names  = array();
	foreach ( $listed as $name ) {
		$name = trim( (string) $name );
		if ( preg_match( '#^[a-z0-9-]+/[a-z0-9-]+$#', $name ) && ! str_starts_with( $name, WPMCP_BRIDGE_ABILITY_CATEGORY . '/' ) ) {
			$names[] = $name;
		}
	}
	return array_slice( array_values( array_unique( $names ) ), 0, WPMCP_BRIDGE_SITE_ABILITIES_MAX );
}

/** Listed abilities that exist on this site, by name. */
function wpmcp_site_abilities(): array {
	if ( ! function_exists( 'wp_get_ability' ) ) {
		return array();
	}
	$found = array();
	foreach ( wpmcp_site_ability_names() as $name ) {
		$ability = wp_get_ability( $name );
		if ( $ability ) {
			$found[ $name ] = $ability;
		}
	}
	return $found;
}

add_action( 'rest_api_init', 'wpmcp_register_site_ability_routes' );

function wpmcp_register_site_ability_routes(): void {
	register_rest_route(
		WPMCP_BRIDGE_NS,
		'/abilities',
		array(
			'methods'             => WP_REST_Server::READABLE,
			'callback'            => 'wpmcp_rest_site_abilities',
			'permission_callback' => 'wpmcp_can_read',
		)
	);
	register_rest_route(
		WPMCP_BRIDGE_NS,
		'/abilities/run',
		array(
			'methods'             => 'POST',
			'callback'            => 'wpmcp_rest_site_ability_run',
			'permission_callback' => 'wpmcp_can_read',
			'args'                => array(
				'name'  => array( 'type' => 'string', 'pattern' => '^[a-z0-9-]+/[a-z0-9-]+$', 'required' => true ),
				// Validated by the ability against its own schema.
				'input' => array(),
			),
		)
	);
}

function wpmcp_rest_site_abilities(): array {
	$prepare = function_exists( 'wp_prepare_json_schema_for_client' ) ? 'wp_prepare_json_schema_for_client' : static fn ( $schema ) => $schema;
	$items   = array();
	foreach ( wpmcp_site_abilities() as $name => $ability ) {
		$meta    = $ability->get_meta();
		$items[] = array(
			'name'          => $name,
			'label'         => $ability->get_label(),
			'description'   => $ability->get_description(),
			'input_schema'  => $ability->get_input_schema() ? $prepare( $ability->get_input_schema() ) : null,
			'output_schema' => $ability->get_output_schema() ? $prepare( $ability->get_output_schema() ) : null,
			'annotations'   => (array) ( $meta['annotations'] ?? array() ),
		);
	}
	return array( 'items' => $items );
}

function wpmcp_rest_site_ability_run( WP_REST_Request $request ) {
	$name      = (string) $request['name'];
	$abilities = wpmcp_site_abilities();
	if ( ! isset( $abilities[ $name ] ) ) {
		return wpmcp_error( 'wpmcp_ability_not_listed', "Ability $name is not in this site's list for wpmcp.", 404 );
	}
	$ability = $abilities[ $name ];
	$result  = $ability->execute( $request->has_param( 'input' ) ? $request['input'] : null );
	if ( is_wp_error( $result ) ) {
		$status = array(
			'ability_invalid_input'        => 400,
			'ability_missing_input_schema' => 400,
			'ability_invalid_permissions'  => 403,
		);
		$code   = $result->get_error_code();
		$data   = $result->get_error_data( $code );
		$known  = is_array( $data ) && ! empty( $data['status'] ) ? (int) $data['status'] : ( $status[ $code ] ?? 500 );
		return wpmcp_error( 'wpmcp_ability_failed', $result->get_error_message(), $known );
	}
	if ( true !== ( $ability->get_meta()['annotations']['readonly'] ?? null ) ) {
		wpmcp_live_record( "ran ability $name", 0 );
	}
	return array(
		'ability' => $name,
		'result'  => $result,
	);
}
