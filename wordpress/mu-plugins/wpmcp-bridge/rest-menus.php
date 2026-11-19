<?php
/**
 * Navigation menus: read (GET /menus, registered in rest-read.php), replace, add items to,
 * and restore.
 *
 * Only menus the site shows can be changed: those assigned to a theme location (per
 * language with Polylang) and those shown by a Navigation Menu widget in a widget area of
 * the theme (a footer's link lists, per language with Polylang's widget language). Never
 * which menu a location or a widget shows: the widget settings are read, not written. A
 * change replaces the menu's items with the list given, after saving the current items
 * (the last 5 versions per menu); adding items keeps the others and skips any already in
 * the menu; restore puts the latest saved version back. Items are typed: a published post
 * or page, a category (both in the menu's language), an https or site-relative link, or an
 * existing item kept as it is (how Polylang's language switcher, or any item of another
 * kind, stays). A language switcher left out is put back at the end.
 *
 * None of this needs edit_theme_options: the routes check the menu themselves and build
 * the items with core's nav menu functions, which do not depend on that capability.
 */

defined( 'ABSPATH' ) || exit;

const WPMCP_BRIDGE_MENU_BACKUPS    = 'wpmcp_menu_backups';
const WPMCP_BRIDGE_MENU_KEEP       = 5;
const WPMCP_BRIDGE_MENU_MAX_ITEMS  = 60;
const WPMCP_BRIDGE_MENU_MAX_ADD    = 20;
const WPMCP_BRIDGE_PLL_SWITCHER    = '#pll_switcher';
const WPMCP_BRIDGE_ITEM_META_KEEP  = array( '_pll_menu_item' );

/** The menu changes: routes of wpmcp/v1 and abilities (see abilities.php). */
function wpmcp_menu_operations(): array {
	$menu_id = array( 'type' => 'integer', 'minimum' => 1, 'required' => true );
	$version = array( 'type' => 'string', 'pattern' => '^[0-9a-f]{16}$', 'required' => true );
	$new     = array(
		'type'                 => 'object',
		'additionalProperties' => false,
		'required'             => array( 'kind' ),
		'properties'           => array(
			'kind'  => array( 'type' => 'string', 'enum' => array( 'post', 'page', 'category', 'link' ) ),
			'id'    => array( 'type' => 'integer', 'minimum' => 1 ),
			'url'   => array( 'type' => 'string', 'maxLength' => 500 ),
			'title' => array( 'type' => 'string', 'maxLength' => 200 ),
		),
	);
	return array(
		array(
			'ability'             => 'wpmcp/apply-menu',
			'label'               => 'Change a menu',
			'description'         => 'Replace the items of a menu shown by a theme location or a menu widget, against its version; the previous items are saved for restore-menu.',
			'route'               => '/menus/(?P<menu_id>\d+)',
			'methods'             => 'POST',
			'annotations'         => wpmcp_annotations( false, true, true ),
			'callback'            => 'wpmcp_rest_menu_apply',
			'permission_callback' => 'wpmcp_can_read',
			'args'                => array(
				'menu_id'          => $menu_id,
				'expected_version' => $version,
				'items'            => array(
					'type'     => 'array',
					'required' => true,
					'maxItems' => WPMCP_BRIDGE_MENU_MAX_ITEMS,
					'items'    => array(
						'type'                 => 'object',
						'additionalProperties' => false,
						'required'             => array( 'kind' ),
						'properties'           => array(
							'kind'    => array( 'type' => 'string', 'enum' => array( 'post', 'page', 'category', 'link', 'keep' ) ),
							'id'      => array( 'type' => 'integer', 'minimum' => 1 ),
							'url'     => array( 'type' => 'string', 'maxLength' => 500 ),
							'title'   => array( 'type' => 'string', 'maxLength' => 200 ),
							'parent'  => array( 'type' => array( 'integer', 'null' ), 'minimum' => 0 ),
						),
					),
				),
			),
		),
		array(
			'ability'             => 'wpmcp/add-menu-items',
			'label'               => 'Add menu items',
			'description'         => 'Add items to a menu shown by a theme location or a menu widget, keeping the others; items already in the menu are skipped. The previous items are saved for restore-menu.',
			'route'               => '/menus/(?P<menu_id>\d+)/items',
			'methods'             => 'POST',
			'annotations'         => wpmcp_annotations( false, true, true ),
			'callback'            => 'wpmcp_rest_menu_add_items',
			'permission_callback' => 'wpmcp_can_read',
			'args'                => array(
				'menu_id'          => $menu_id,
				'expected_version' => $version,
				'items'            => array(
					'type'     => 'array',
					'required' => true,
					'minItems' => 1,
					'maxItems' => WPMCP_BRIDGE_MENU_MAX_ADD,
					'items'    => $new,
				),
				'position'         => array( 'type' => 'integer', 'minimum' => 0 ),
			),
		),
		array(
			'ability'             => 'wpmcp/restore-menu',
			'label'               => 'Restore a menu',
			'description'         => 'Put a menu back to the items saved before its last change by apply-menu or add-menu-items.',
			'route'               => '/menus/(?P<menu_id>\d+)/restore',
			'methods'             => 'POST',
			'annotations'         => wpmcp_annotations( false, true, false ),
			'callback'            => 'wpmcp_rest_menu_restore',
			'permission_callback' => 'wpmcp_can_read',
			'args'                => array( 'menu_id' => $menu_id ),
		),
	);
}

// ----------------------------------------------------------------------------- read

/** Menus the site shows: by theme location, then by menu widget. */
function wpmcp_assigned_menus(): array {
	return array_merge( wpmcp_location_menus(), wpmcp_widget_menus() );
}

/** Menus shown by a theme location: location, language (with Polylang) and menu ID. */
function wpmcp_location_menus(): array {
	$assigned = array();
	$options  = get_option( 'polylang' );
	$theme    = get_option( 'stylesheet' );
	if ( is_array( $options ) && isset( $options['nav_menus'][ $theme ] ) ) {
		foreach ( $options['nav_menus'][ $theme ] as $location => $by_lang ) {
			foreach ( (array) $by_lang as $lang => $menu_id ) {
				$assigned[] = array( 'location' => $location, 'lang' => $lang, 'menu_id' => (int) $menu_id );
			}
		}
	} else {
		foreach ( get_nav_menu_locations() as $location => $menu_id ) {
			$assigned[] = array( 'location' => $location, 'lang' => null, 'menu_id' => (int) $menu_id );
		}
	}
	return $assigned;
}

/**
 * Menus shown by a Navigation Menu widget in a widget area the theme registers (not the
 * inactive widgets): area, its name, the widget's title, its language (Polylang's widget
 * language; null when it shows in every language) and the menu ID.
 */
function wpmcp_widget_menus(): array {
	global $wp_registered_sidebars;
	$instances = get_option( 'widget_nav_menu', array() );
	$shown     = array();
	foreach ( wp_get_sidebars_widgets() as $area => $widgets ) {
		if ( 'wp_inactive_widgets' === $area || ! is_array( $widgets ) || ! is_registered_sidebar( $area ) ) {
			continue;
		}
		foreach ( $widgets as $widget ) {
			if ( ! preg_match( '/^nav_menu-(\d+)$/', (string) $widget, $number ) ) {
				continue;
			}
			$instance = is_array( $instances ) ? ( $instances[ (int) $number[1] ] ?? null ) : null;
			if ( ! is_array( $instance ) || empty( $instance['nav_menu'] ) ) {
				continue;
			}
			$shown[] = array(
				'location'         => null,
				'widget_area'      => (string) $area,
				'widget_area_name' => (string) ( $wp_registered_sidebars[ $area ]['name'] ?? $area ),
				'widget_title'     => (string) ( $instance['title'] ?? '' ),
				'lang'             => empty( $instance['pll_lang'] ) ? null : (string) $instance['pll_lang'],
				'menu_id'          => (int) $instance['nav_menu'],
			);
		}
	}
	return $shown;
}

/** What kind of item this is, in the terms apply_menu uses. */
function wpmcp_menu_item_kind( WP_Post $item ): string {
	if ( 'custom' === $item->type ) {
		return WPMCP_BRIDGE_PLL_SWITCHER === $item->url ? 'language_switcher' : 'link';
	}
	if ( 'post_type' === $item->type && in_array( $item->object, WPMCP_BRIDGE_TYPES, true ) ) {
		return $item->object;
	}
	if ( 'taxonomy' === $item->type && 'category' === $item->object ) {
		return 'category';
	}
	return 'other';
}

/** The items of a menu, in order, with parents as item IDs. */
function wpmcp_menu_items( int $menu_id ): array {
	$items = array();
	foreach ( (array) wp_get_nav_menu_items( $menu_id, array( 'post_status' => 'any' ) ) as $item ) {
		$kind    = wpmcp_menu_item_kind( $item );
		$items[] = array(
			'id'        => (int) $item->ID,
			'kind'      => $kind,
			'title'     => html_entity_decode( wp_strip_all_tags( $item->title ), ENT_QUOTES | ENT_HTML5, 'UTF-8' ),
			'url'       => $item->url,
			// A link's object is the item itself: no object to point at.
			'object_id' => 'custom' === $item->type ? null : (int) $item->object_id,
			'parent'    => $item->menu_item_parent ? (int) $item->menu_item_parent : null,
			'order'     => (int) $item->menu_order,
		);
	}
	return $items;
}

/** Fingerprint of a menu's items; parents by position, since a restore gives new item IDs. */
function wpmcp_menu_version( array $items ): string {
	$position = array_flip( array_column( $items, 'id' ) );
	$state    = array_map(
		static fn ( $i ) => array( $i['kind'], $i['object_id'], $i['url'], $i['title'], null === $i['parent'] ? null : ( $position[ $i['parent'] ] ?? -1 ) ),
		$items
	);
	return substr( hash( 'sha256', wp_json_encode( $state ) ), 0, 16 );
}

function wpmcp_rest_menus(): array {
	$menus = array();
	foreach ( wpmcp_assigned_menus() as $slot ) {
		$menu = $slot['menu_id'] ? wp_get_nav_menu_object( $slot['menu_id'] ) : false;
		if ( ! $menu ) {
			$menus[] = $slot + array( 'name' => null, 'items' => array() );
			continue;
		}
		$items   = wpmcp_menu_items( $menu->term_id );
		$backups = get_option( WPMCP_BRIDGE_MENU_BACKUPS, array() );
		$menus[] = $slot + array(
			'name'            => $menu->name,
			'items'           => $items,
			'version'         => wpmcp_menu_version( $items ),
			'saved_versions'  => is_array( $backups ) ? count( $backups[ $menu->term_id ] ?? array() ) : 0,
		);
	}
	return array( 'theme' => get_option( 'stylesheet' ), 'menus' => $menus );
}

// ---------------------------------------------------------------------------- write

/** The location slot of an assigned menu, or an error for any other menu. */
function wpmcp_menu_slot( int $menu_id ) {
	foreach ( wpmcp_assigned_menus() as $slot ) {
		if ( $slot['menu_id'] === $menu_id && wp_get_nav_menu_object( $menu_id ) ) {
			return $slot;
		}
	}
	return wpmcp_error( 'wpmcp_bad_menu', "Menu $menu_id is not shown by a theme location or a menu widget (see get_menus): only those can be changed.", 404 );
}

/** Full copy of an existing item, enough to create it again. */
function wpmcp_menu_item_spec( WP_Post $item ): array {
	$meta = array();
	foreach ( WPMCP_BRIDGE_ITEM_META_KEEP as $key ) {
		$value = get_post_meta( $item->ID, $key, true );
		if ( '' !== $value ) {
			$meta[ $key ] = $value;
		}
	}
	return array(
		'data' => array(
			'menu-item-type'        => $item->type,
			'menu-item-object'      => $item->object,
			'menu-item-object-id'   => (int) $item->object_id,
			'menu-item-url'         => $item->url,
			'menu-item-title'       => $item->post_title,
			'menu-item-attr-title'  => $item->attr_title,
			'menu-item-description' => $item->description,
			'menu-item-classes'     => implode( ' ', (array) $item->classes ),
			'menu-item-xfn'         => $item->xfn,
			'menu-item-target'      => $item->target,
			'menu-item-status'      => 'publish',
		),
		'meta' => $meta,
	);
}

/** The current items as specs with parents as list indexes (what a backup holds). */
function wpmcp_menu_snapshot( int $menu_id ): array {
	$specs = array();
	$index = array();
	foreach ( (array) wp_get_nav_menu_items( $menu_id, array( 'post_status' => 'any' ) ) as $position => $item ) {
		$index[ (int) $item->ID ] = $position;
		$spec                     = wpmcp_menu_item_spec( $item );
		$spec['parent']           = $item->menu_item_parent ? ( $index[ (int) $item->menu_item_parent ] ?? null ) : null;
		$specs[]                  = $spec;
	}
	return $specs;
}

/** Specs for the requested items, checked; or an error naming the first bad one. */
function wpmcp_menu_requested_specs( array $requested, int $menu_id, ?string $lang ) {
	$existing = array();
	foreach ( (array) wp_get_nav_menu_items( $menu_id, array( 'post_status' => 'any' ) ) as $item ) {
		$existing[ (int) $item->ID ] = $item;
	}
	$specs    = array();
	$switcher = false;
	foreach ( array_values( $requested ) as $n => $wanted ) {
		$at    = 'Item ' . ( $n + 1 );
		$kind  = (string) $wanted['kind'];
		$title = isset( $wanted['title'] ) ? trim( (string) $wanted['title'] ) : '';
		$spec  = null;
		if ( 'keep' === $kind ) {
			$item = $existing[ (int) ( $wanted['id'] ?? 0 ) ] ?? null;
			if ( ! $item ) {
				return wpmcp_error( 'wpmcp_bad_menu_item', "$at: keep needs the id of an item of this menu (see get_menus)." );
			}
			$spec     = wpmcp_menu_item_spec( $item );
			$switcher = $switcher || WPMCP_BRIDGE_PLL_SWITCHER === $item->url;
			if ( '' !== $title ) {
				$spec['data']['menu-item-title'] = $title;
			}
		} else {
			$spec = wpmcp_menu_new_item_spec( $wanted, $at, $lang );
			if ( is_wp_error( $spec ) ) {
				return $spec;
			}
		}
		$parent = $wanted['parent'] ?? null;
		if ( null !== $parent && ( (int) $parent >= $n ) ) {
			return wpmcp_error( 'wpmcp_bad_menu_item', "$at: parent must be the position (from 0) of an earlier item." );
		}
		$spec['parent'] = null === $parent ? null : (int) $parent;
		$specs[]        = $spec;
	}
	$notes = array();
	if ( ! $switcher ) {
		foreach ( $existing as $item ) {
			if ( WPMCP_BRIDGE_PLL_SWITCHER === $item->url ) {
				$specs[] = wpmcp_menu_item_spec( $item ) + array( 'parent' => null );
				$notes[] = 'The language switcher was kept, at the end of the menu.';
				break;
			}
		}
	}
	return array( $specs, $notes );
}

/** The spec of a new post, page, category or link item, checked; or an error. */
function wpmcp_menu_new_item_spec( array $wanted, string $at, ?string $lang ) {
	$kind  = (string) $wanted['kind'];
	$title = isset( $wanted['title'] ) ? trim( (string) $wanted['title'] ) : '';
	if ( in_array( $kind, array( 'post', 'page' ), true ) ) {
		$post = get_post( (int) ( $wanted['id'] ?? 0 ) );
		if ( ! $post || $post->post_type !== $kind || 'publish' !== $post->post_status ) {
			return wpmcp_error( 'wpmcp_bad_menu_item', "$at: no published $kind with ID " . (int) ( $wanted['id'] ?? 0 ) . '.' );
		}
		if ( $lang && wpmcp_post_lang( $post->ID ) !== $lang ) {
			return wpmcp_error( 'wpmcp_bad_menu_item', "$at: {$post->ID} is in " . ( wpmcp_post_lang( $post->ID ) ?? 'no language' ) . ", the menu is in $lang." );
		}
		return array(
			'data' => array(
				'menu-item-type'      => 'post_type',
				'menu-item-object'    => $kind,
				'menu-item-object-id' => $post->ID,
				'menu-item-title'     => $title,
				'menu-item-status'    => 'publish',
			),
			'meta' => array(),
		);
	}
	if ( 'category' === $kind ) {
		$term = get_term( (int) ( $wanted['id'] ?? 0 ), 'category' );
		if ( ! $term instanceof WP_Term ) {
			return wpmcp_error( 'wpmcp_bad_menu_item', "$at: no category with ID " . (int) ( $wanted['id'] ?? 0 ) . '.' );
		}
		if ( $lang && wpmcp_term_lang( $term->term_id ) !== $lang ) {
			return wpmcp_error( 'wpmcp_bad_menu_item', "$at: category {$term->term_id} is not in $lang." );
		}
		return array(
			'data' => array(
				'menu-item-type'      => 'taxonomy',
				'menu-item-object'    => 'category',
				'menu-item-object-id' => $term->term_id,
				'menu-item-title'     => $title,
				'menu-item-status'    => 'publish',
			),
			'meta' => array(),
		);
	}
	// A link.
	$url = trim( (string) ( $wanted['url'] ?? '' ) );
	if ( ! preg_match( '#^(https://[^\s/]+(/\S*)?|/\S*)$#', $url ) || WPMCP_BRIDGE_PLL_SWITCHER === $url ) {
		return wpmcp_error( 'wpmcp_bad_menu_item', "$at: a link needs an https:// or site-relative (/...) url." );
	}
	if ( '' === $title ) {
		return wpmcp_error( 'wpmcp_bad_menu_item', "$at: a link needs a title." );
	}
	return array(
		'data' => array(
			'menu-item-type'   => 'custom',
			'menu-item-object' => 'custom',
			'menu-item-url'    => esc_url_raw( $url ),
			'menu-item-title'  => $title,
			'menu-item-status' => 'publish',
		),
		'meta' => array(),
	);
}

/** Replaces every item of the menu with $specs (parents as indexes into $specs). */
function wpmcp_menu_replace( int $menu_id, array $specs ): void {
	foreach ( (array) wp_get_nav_menu_items( $menu_id, array( 'post_status' => 'any' ) ) as $item ) {
		wp_delete_post( $item->ID, true ); // a menu item, not content: the menu was saved first
	}
	$created = array();
	foreach ( $specs as $position => $spec ) {
		$data                          = $spec['data'];
		$data['menu-item-position']    = $position + 1;
		$data['menu-item-parent-id']   = null === $spec['parent'] ? 0 : ( $created[ $spec['parent'] ] ?? 0 );
		$id                            = wp_update_nav_menu_item( $menu_id, 0, $data );
		$created[ $position ]          = is_wp_error( $id ) ? 0 : (int) $id;
		foreach ( $spec['meta'] as $key => $value ) {
			if ( $created[ $position ] ) {
				update_post_meta( $created[ $position ], $key, $value );
			}
		}
	}
	wp_cache_delete( $menu_id, 'nav_menu' );
}

function wpmcp_menu_result( array $slot, array $notes = array() ): array {
	$items   = wpmcp_menu_items( $slot['menu_id'] );
	$backups = get_option( WPMCP_BRIDGE_MENU_BACKUPS, array() );
	$result  = $slot + array(
		'name'           => wp_get_nav_menu_object( $slot['menu_id'] )->name,
		'items'          => $items,
		'version'        => wpmcp_menu_version( $items ),
		'saved_versions' => is_array( $backups ) ? count( $backups[ $slot['menu_id'] ] ?? array() ) : 0,
	);
	if ( $notes ) {
		$result['notes'] = $notes;
	}
	return $result;
}

function wpmcp_rest_menu_apply( WP_REST_Request $request ) {
	$menu_id = (int) $request['menu_id'];
	$slot    = wpmcp_menu_slot( $menu_id );
	if ( is_wp_error( $slot ) ) {
		return $slot;
	}
	if ( ! hash_equals( wpmcp_menu_version( wpmcp_menu_items( $menu_id ) ), (string) $request['expected_version'] ) ) {
		return wpmcp_error( 'wpmcp_stale', "Menu $menu_id changed since that version was read. Read it again with get_menus.", 409 );
	}
	$checked = wpmcp_menu_requested_specs( (array) $request['items'], $menu_id, $slot['lang'] );
	if ( is_wp_error( $checked ) ) {
		return $checked;
	}
	list( $specs, $notes ) = $checked;

	wpmcp_menu_backup( $menu_id );
	wpmcp_menu_replace( $menu_id, $specs );
	wpmcp_live_record( 'changed menu', $menu_id );
	$notes[] = 'The previous version was saved: restore_menu puts it back.';
	return wpmcp_menu_result( $slot, $notes );
}

/** Saves the current items of a menu for restore-menu (the last WPMCP_BRIDGE_MENU_KEEP). */
function wpmcp_menu_backup( int $menu_id ): void {
	$backups             = get_option( WPMCP_BRIDGE_MENU_BACKUPS, array() );
	$backups             = is_array( $backups ) ? $backups : array();
	$saved               = $backups[ $menu_id ] ?? array();
	array_unshift( $saved, array( 'time' => time(), 'items' => wpmcp_menu_snapshot( $menu_id ) ) );
	$backups[ $menu_id ] = array_slice( $saved, 0, WPMCP_BRIDGE_MENU_KEEP );
	update_option( WPMCP_BRIDGE_MENU_BACKUPS, $backups, false );
}

/** Whether one of $existing already shows what $spec adds: the same object, or the same link. */
function wpmcp_menu_has( array $existing, array $spec ): bool {
	$data = $spec['data'];
	foreach ( $existing as $item ) {
		$same = 'custom' === $data['menu-item-type']
			? 'custom' === $item->type && wpmcp_link_key( (string) $item->url ) === wpmcp_link_key( $data['menu-item-url'] )
			: $item->type === $data['menu-item-type'] && $item->object === $data['menu-item-object'] && (int) $item->object_id === (int) $data['menu-item-object-id'];
		if ( $same ) {
			return true;
		}
	}
	return false;
}

/**
 * Adds items to a menu without touching the others: at `position` (an index in the item
 * list get_menus shows), by default at the end, before a language switcher that ends the
 * menu. Items the menu already has (same post, page, category or link) are skipped; when
 * every one is, nothing is written.
 */
function wpmcp_rest_menu_add_items( WP_REST_Request $request ) {
	$menu_id = (int) $request['menu_id'];
	$slot    = wpmcp_menu_slot( $menu_id );
	if ( is_wp_error( $slot ) ) {
		return $slot;
	}
	if ( ! hash_equals( wpmcp_menu_version( wpmcp_menu_items( $menu_id ) ), (string) $request['expected_version'] ) ) {
		return wpmcp_error( 'wpmcp_stale', "Menu $menu_id changed since that version was read. Read it again with get_menus.", 409 );
	}
	$existing = array_values( (array) wp_get_nav_menu_items( $menu_id, array( 'post_status' => 'any' ) ) );
	$specs    = array();
	$skipped  = 0;
	foreach ( array_values( (array) $request['items'] ) as $n => $wanted ) {
		$spec = wpmcp_menu_new_item_spec( $wanted, 'Item ' . ( $n + 1 ), $slot['lang'] );
		if ( is_wp_error( $spec ) ) {
			return $spec;
		}
		$known = array_map( static fn ( $s ) => (object) array( 'type' => $s['data']['menu-item-type'], 'object' => $s['data']['menu-item-object'], 'object_id' => $s['data']['menu-item-object-id'] ?? 0, 'url' => $s['data']['menu-item-url'] ?? '' ), $specs );
		if ( wpmcp_menu_has( array_merge( $existing, $known ), $spec ) ) {
			++$skipped;
			continue;
		}
		$specs[] = $spec;
	}
	if ( ! $specs ) {
		return wpmcp_menu_result( $slot, array( 'Every item is already in the menu: nothing changed.' ) );
	}
	$count = count( $existing );
	if ( $count + count( $specs ) > WPMCP_BRIDGE_MENU_MAX_ITEMS ) {
		return wpmcp_error( 'wpmcp_menu_full', 'A menu can have at most ' . WPMCP_BRIDGE_MENU_MAX_ITEMS . ' items here.' );
	}
	if ( $request->has_param( 'position' ) ) {
		$at = min( (int) $request['position'], $count );
	} else {
		$at = $count && WPMCP_BRIDGE_PLL_SWITCHER === $existing[ $count - 1 ]->url ? $count - 1 : $count;
	}

	wpmcp_menu_backup( $menu_id );
	$order = 1;
	$add   = static function ( array $spec ) use ( $menu_id, &$order ): void {
		$data                        = $spec['data'];
		$data['menu-item-position']  = $order++;
		$data['menu-item-parent-id'] = 0;
		wp_update_nav_menu_item( $menu_id, 0, $data );
	};
	foreach ( $existing as $index => $item ) {
		if ( $index === $at ) {
			array_walk( $specs, $add );
		}
		if ( (int) $item->menu_order !== $order ) {
			wp_update_post( array( 'ID' => $item->ID, 'menu_order' => $order ) );
		}
		++$order;
	}
	if ( $at >= $count ) {
		array_walk( $specs, $add );
	}
	wp_cache_delete( $menu_id, 'nav_menu' );
	wpmcp_live_record( 'added menu items', $menu_id );
	$notes = array( sprintf( 'Added %d item(s).', count( $specs ) ) );
	if ( $skipped ) {
		$notes[] = "$skipped item(s) already in the menu were skipped.";
	}
	$notes[] = 'The previous version was saved: restore_menu puts it back.';
	return wpmcp_menu_result( $slot, $notes );
}

function wpmcp_rest_menu_restore( WP_REST_Request $request ) {
	$menu_id = (int) $request['menu_id'];
	$slot    = wpmcp_menu_slot( $menu_id );
	if ( is_wp_error( $slot ) ) {
		return $slot;
	}
	$backups = get_option( WPMCP_BRIDGE_MENU_BACKUPS, array() );
	$saved   = is_array( $backups ) ? ( $backups[ $menu_id ] ?? array() ) : array();
	if ( ! $saved ) {
		return wpmcp_error( 'wpmcp_no_backup', "There is no saved version of menu $menu_id.", 404 );
	}
	$latest              = array_shift( $saved );
	$backups[ $menu_id ] = $saved;
	update_option( WPMCP_BRIDGE_MENU_BACKUPS, $backups, false );
	wpmcp_menu_replace( $menu_id, $latest['items'] );
	wpmcp_live_record( 'restored menu', $menu_id );
	return wpmcp_menu_result( $slot, array( 'Restored the version saved at ' . gmdate( 'Y-m-d H:i', (int) $latest['time'] ) . ' UTC.' ) );
}
