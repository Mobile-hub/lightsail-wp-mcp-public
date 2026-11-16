<?php
/**
 * Live routes of wpmcp/v1: what changes the site visitors see. Publish, schedule,
 * unpublish, edit published items, restore a revision, trash and untrash, link
 * translations.
 *
 * Safety nets, since nobody reviews these changes first:
 * - every change names the version it was based on and is refused if the item changed, or
 *   if someone has it open in the editor (wpmcp_edit_guard);
 * - the front page, the posts page, the privacy policy page, their translations and
 *   WPMCP_BRIDGE_PROTECTED_IDS (wp-config.php, comma-separated) cannot be unpublished,
 *   trashed or changed here;
 * - the state before a live edit or a restore is saved as a revision first, and returned;
 * - nothing is ever deleted: trash only (core's trash keeps items for EMPTY_TRASH_DAYS);
 * - each change is recorded (action, ID, time; no content) and mailed to the site's
 *   admin e-mail once an hour.
 */

defined( 'ABSPATH' ) || exit;

const WPMCP_BRIDGE_LIVE         = array( 'publish', 'future', 'private' );
const WPMCP_BRIDGE_LOG_OPTION   = 'wpmcp_live_log';
const WPMCP_BRIDGE_LOG_MAX      = 200;
const WPMCP_BRIDGE_DIGEST_EVENT = 'wpmcp_live_digest';

add_action( 'rest_api_init', 'wpmcp_register_live_routes' );

function wpmcp_register_live_routes(): void {
	$version = array( 'type' => 'string', 'pattern' => '^[0-9a-f]{16}$', 'required' => true );
	$id      = array( 'type' => 'integer', 'minimum' => 1, 'required' => true );
	$item    = static fn ( string $path, string $callback, array $args = array() ) => register_rest_route(
		WPMCP_BRIDGE_NS,
		'/content/(?P<id>\d+)/' . $path,
		array(
			'methods'             => 'POST',
			'callback'            => $callback,
			'permission_callback' => 'wpmcp_can_edit_item',
			'args'                => $args + array( 'id' => $id ),
		)
	);
	$item( 'publish', 'wpmcp_rest_publish', array( 'expected_version' => $version ) );
	$item(
		'schedule',
		'wpmcp_rest_schedule',
		array(
			'expected_version' => $version,
			'publish_at'       => array( 'type' => 'string', 'maxLength' => 40, 'required' => true ),
		)
	);
	$item( 'unpublish', 'wpmcp_rest_unpublish', array( 'expected_version' => $version ) );
	$item(
		'live',
		'wpmcp_rest_live_update',
		array( 'expected_version' => $version ) + wpmcp_content_field_args()
	);
	$item(
		'restore-revision',
		'wpmcp_rest_restore_revision',
		array(
			'expected_version' => $version,
			'revision_id'      => $id,
		)
	);
	$item( 'trash', 'wpmcp_rest_trash', array( 'expected_version' => $version ) );
	$item( 'untrash', 'wpmcp_rest_untrash' );

	register_rest_route(
		WPMCP_BRIDGE_NS,
		'/translations',
		array(
			'methods'             => 'POST',
			'callback'            => 'wpmcp_rest_link_translations',
			'permission_callback' => 'wpmcp_can_read',
			'args'                => array(
				'items' => array(
					'type'                 => 'object',
					'required'             => true,
					'minProperties'        => 2,
					'maxProperties'        => 10,
					'additionalProperties' => array( 'type' => 'integer', 'minimum' => 1 ),
				),
			),
		)
	);
}

// --------------------------------------------------------------------------- checks

/** IDs nobody may unpublish, trash or change through these routes, translations included. */
function wpmcp_protected_ids(): array {
	$ids = array(
		(int) get_option( 'page_on_front' ),
		(int) get_option( 'page_for_posts' ),
		(int) get_option( 'wp_page_for_privacy_policy' ),
	);
	if ( defined( 'WPMCP_BRIDGE_PROTECTED_IDS' ) ) {
		$ids = array_merge( $ids, array_map( 'intval', explode( ',', (string) WPMCP_BRIDGE_PROTECTED_IDS ) ) );
	}
	$all = array();
	foreach ( array_filter( $ids ) as $protected ) {
		$all[] = $protected;
		foreach ( wpmcp_post_translations( $protected ) as $member ) {
			$all[] = $member;
		}
	}
	return array_values( array_unique( $all ) );
}

function wpmcp_check_protected( WP_Post $post ): ?WP_Error {
	if ( in_array( (int) $post->ID, wpmcp_protected_ids(), true ) ) {
		return wpmcp_error( 'wpmcp_protected', "Item {$post->ID} is protected (front page, posts page, privacy policy or set by the site owner): change it in wp-admin.", 403 );
	}
	return null;
}

function wpmcp_check_status( WP_Post $post, array $allowed, string $what ): ?WP_Error {
	if ( ! in_array( $post->post_status, $allowed, true ) ) {
		return wpmcp_error( 'wpmcp_bad_status', "Item {$post->ID} is {$post->post_status}: $what.", 409 );
	}
	return null;
}

/** Notes about translations of $post that visitors cannot see yet. */
function wpmcp_translation_notes( WP_Post $post ): array {
	$notes = array();
	foreach ( wpmcp_post_translations( $post->ID ) as $lang => $member ) {
		if ( $member !== (int) $post->ID && 'publish' !== get_post_status( $member ) ) {
			$notes[] = "Its $lang translation ($member) is " . get_post_status( $member ) . ': visitors see no link to it until it is published.';
		}
	}
	return $notes;
}

/** The parts of an item a live edit may change that revisions do not keep. */
function wpmcp_live_state( WP_Post $post ): array {
	return array(
		'slug'           => $post->post_name,
		'parent'         => $post->post_parent ? (int) $post->post_parent : null,
		'categories'     => 'post' === $post->post_type ? array_column( wpmcp_terms_of( $post->ID, 'category' ), 'id' ) : array(),
		'featured_media' => (int) get_post_thumbnail_id( $post ) ?: null,
		'seo'            => wpmcp_seo( $post->ID ),
	);
}

/** Saves the current state as a revision (if it differs from the latest) and returns its ID. */
function wpmcp_keep_revision( WP_Post $post ): ?int {
	if ( ! wp_revisions_enabled( $post ) ) {
		return null;
	}
	$saved = wp_save_post_revision( $post->ID );
	if ( $saved && ! is_wp_error( $saved ) ) {
		return (int) $saved;
	}
	$latest = wp_get_post_revisions( $post->ID, array( 'posts_per_page' => 1 ) );
	return $latest ? (int) array_key_first( $latest ) : null;
}

// ----------------------------------------------------------------------- the changes

function wpmcp_rest_publish( WP_REST_Request $request ) {
	$post  = get_post( (int) $request['id'] );
	$check = wpmcp_check_status( $post, WPMCP_BRIDGE_EDITABLE, 'only drafts and pending items can be published' )
		?? wpmcp_edit_guard( $post, $request );
	if ( $check ) {
		return $check;
	}
	if ( '' === trim( $post->post_title ) ) {
		return wpmcp_error( 'wpmcp_no_title', 'Give the item a title before publishing it.' );
	}
	$result = wp_update_post(
		array(
			'ID'            => $post->ID,
			'post_status'   => 'publish',
			'post_date'     => current_time( 'mysql' ),
			'post_date_gmt' => current_time( 'mysql', true ),
			'edit_date'     => true,
		),
		true
	);
	if ( is_wp_error( $result ) ) {
		return wpmcp_error( 'wpmcp_update_failed', $result->get_error_message(), 500 );
	}
	wpmcp_live_record( 'published', $post->ID );
	return wpmcp_write_result( $post->ID, wpmcp_translation_notes( get_post( $post->ID ) ) );
}

function wpmcp_rest_schedule( WP_REST_Request $request ) {
	$post  = get_post( (int) $request['id'] );
	$check = wpmcp_check_status( $post, array( 'draft', 'pending', 'future' ), 'only drafts, pending and scheduled items can be scheduled' )
		?? wpmcp_edit_guard( $post, $request );
	if ( $check ) {
		return $check;
	}
	$gmt  = wpmcp_gmt( (string) $request['publish_at'] );
	$when = null === $gmt ? false : strtotime( $gmt . ' UTC' );
	if ( false === $when || $when < time() + 5 * MINUTE_IN_SECONDS || $when > time() + 366 * DAY_IN_SECONDS ) {
		return wpmcp_error( 'wpmcp_bad_date', 'publish_at must be a date between 5 minutes and one year from now, for example 2026-12-01T09:00:00Z.' );
	}
	if ( '' === trim( $post->post_title ) ) {
		return wpmcp_error( 'wpmcp_no_title', 'Give the item a title before scheduling it.' );
	}
	$result = wp_update_post(
		array(
			'ID'            => $post->ID,
			'post_status'   => 'future',
			'post_date'     => get_date_from_gmt( $gmt ),
			'post_date_gmt' => $gmt,
			'edit_date'     => true,
		),
		true
	);
	if ( is_wp_error( $result ) ) {
		return wpmcp_error( 'wpmcp_update_failed', $result->get_error_message(), 500 );
	}
	wpmcp_live_record( 'scheduled', $post->ID );
	return wpmcp_write_result( $post->ID, wpmcp_translation_notes( get_post( $post->ID ) ) );
}

function wpmcp_rest_unpublish( WP_REST_Request $request ) {
	$post  = get_post( (int) $request['id'] );
	$check = wpmcp_check_status( $post, array( 'publish', 'future' ), 'only published or scheduled items can be unpublished' )
		?? wpmcp_check_protected( $post )
		?? wpmcp_edit_guard( $post, $request );
	if ( $check ) {
		return $check;
	}
	$result = wp_update_post( array( 'ID' => $post->ID, 'post_status' => 'draft' ), true );
	if ( is_wp_error( $result ) ) {
		return wpmcp_error( 'wpmcp_update_failed', $result->get_error_message(), 500 );
	}
	wpmcp_live_record( 'unpublished', $post->ID );
	return wpmcp_write_result( $post->ID );
}

function wpmcp_rest_live_update( WP_REST_Request $request ) {
	$post  = get_post( (int) $request['id'] );
	$check = wpmcp_check_status( $post, WPMCP_BRIDGE_LIVE, 'use update_draft for drafts' )
		?? wpmcp_check_protected( $post )
		?? wpmcp_edit_guard( $post, $request );
	if ( $check ) {
		return $check;
	}
	$before   = wpmcp_live_state( $post );
	$revision = wpmcp_keep_revision( $post );
	$result   = wpmcp_apply_content_update( $post, $request );
	if ( is_wp_error( $result ) ) {
		return $result;
	}
	wpmcp_live_record( 'edited', $post->ID );
	$result['before'] = $before + array( 'revision_id' => $revision );
	return $result;
}

function wpmcp_rest_restore_revision( WP_REST_Request $request ) {
	$post     = get_post( (int) $request['id'] );
	$revision_id = (int) $request['revision_id'];
	$revision    = wp_get_post_revision( $revision_id ); // takes its argument by reference
	if ( ! $revision || (int) $revision->post_parent !== (int) $post->ID ) {
		return wpmcp_error( 'wpmcp_bad_revision', "Revision {$request['revision_id']} is not one of item {$post->ID} (see list_revisions)." );
	}
	$check = wpmcp_check_status( $post, array_merge( WPMCP_BRIDGE_LIVE, WPMCP_BRIDGE_EDITABLE ), 'restore it from the trash first' )
		?? wpmcp_check_protected( $post )
		?? wpmcp_edit_guard( $post, $request );
	if ( $check ) {
		return $check;
	}
	$kept = wpmcp_keep_revision( $post );
	if ( ! wp_restore_post_revision( $revision->ID ) ) {
		return wpmcp_error( 'wpmcp_update_failed', 'The revision could not be restored.', 500 );
	}
	if ( in_array( $post->post_status, WPMCP_BRIDGE_LIVE, true ) ) {
		wpmcp_live_record( 'restored revision', $post->ID );
	}
	$result           = wpmcp_write_result( $post->ID, array( 'Title, content and excerpt come from the revision; categories, featured image and SEO fields did not change.' ) );
	$result['before'] = array( 'revision_id' => $kept );
	return $result;
}

function wpmcp_rest_trash( WP_REST_Request $request ) {
	$post  = get_post( (int) $request['id'] );
	$check = wpmcp_check_status( $post, array_merge( WPMCP_BRIDGE_LIVE, WPMCP_BRIDGE_EDITABLE ), 'it is already in the trash' )
		?? wpmcp_check_protected( $post )
		?? wpmcp_edit_guard( $post, $request );
	if ( $check ) {
		return $check;
	}
	$notes = array();
	foreach ( wpmcp_post_translations( $post->ID ) as $lang => $member ) {
		if ( $member !== (int) $post->ID ) {
			$notes[] = "Its $lang translation ($member) was not trashed.";
		}
	}
	$was_live = in_array( $post->post_status, WPMCP_BRIDGE_LIVE, true );
	if ( ! wp_trash_post( $post->ID ) ) {
		return wpmcp_error( 'wpmcp_update_failed', 'The item could not be moved to the trash.', 500 );
	}
	if ( $was_live ) {
		wpmcp_live_record( 'trashed', $post->ID );
	}
	$notes[] = 'In the trash: restore_from_trash brings it back as a draft.';
	return wpmcp_write_result( $post->ID, $notes );
}

function wpmcp_rest_untrash( WP_REST_Request $request ) {
	$post  = get_post( (int) $request['id'] );
	$check = wpmcp_check_status( $post, array( 'trash' ), 'it is not in the trash' ) ?? wpmcp_writes_blocked();
	if ( $check ) {
		return $check;
	}
	// Core restores to draft (wp_untrash_post_status), so nothing becomes visible here.
	if ( ! wp_untrash_post( $post->ID ) ) {
		return wpmcp_error( 'wpmcp_update_failed', 'The item could not be restored.', 500 );
	}
	return wpmcp_write_result( $post->ID, array( 'Restored as a draft: publish it again to show it.' ) );
}

/**
 * Links items as translations of each other. Existing links are kept: the groups of every
 * item given are merged, and the request is refused if two different items would end up
 * in the same language.
 */
function wpmcp_rest_link_translations( WP_REST_Request $request ) {
	if ( ! wpmcp_has_polylang() ) {
		return wpmcp_error( 'wpmcp_no_polylang', 'Polylang is not active.' );
	}
	$blocked = wpmcp_writes_blocked();
	if ( $blocked ) {
		return $blocked;
	}
	$type   = null;
	$merged = array();
	foreach ( (array) $request['items'] as $lang => $id ) {
		$lang = wpmcp_lang_arg( (string) $lang );
		if ( is_wp_error( $lang ) || '' === $lang ) {
			return is_wp_error( $lang ) ? $lang : wpmcp_error( 'wpmcp_bad_lang', 'Each key must be a language.' );
		}
		$post = get_post( (int) $id );
		if ( ! $post || ! in_array( $post->post_type, WPMCP_BRIDGE_TYPES, true ) || 'trash' === $post->post_status || ! current_user_can( 'edit_post', $post->ID ) ) {
			return wpmcp_error( 'wpmcp_not_found', "No post or page with ID $id.", 404 );
		}
		$type = $type ?? $post->post_type;
		if ( $post->post_type !== $type ) {
			return wpmcp_error( 'wpmcp_mixed_types', 'Posts and pages cannot be translations of each other.' );
		}
		if ( wpmcp_post_lang( $post->ID ) !== $lang ) {
			return wpmcp_error( 'wpmcp_lang_mismatch', "Item {$post->ID} is in " . ( wpmcp_post_lang( $post->ID ) ?? 'no language' ) . ", not $lang." );
		}
		foreach ( wpmcp_post_translations( $post->ID ) + array( $lang => (int) $post->ID ) as $member_lang => $member ) {
			if ( isset( $merged[ $member_lang ] ) && $merged[ $member_lang ] !== $member ) {
				return wpmcp_error( 'wpmcp_translation_conflict', "Both {$merged[ $member_lang ]} and $member would be the $member_lang translation. Unlink one in wp-admin first.", 409 );
			}
			$merged[ $member_lang ] = $member;
		}
	}
	ksort( $merged );
	foreach ( $merged as $member ) {
		if ( in_array( $member, wpmcp_protected_ids(), true ) && wpmcp_post_translations( $member ) !== $merged ) {
			return wpmcp_error( 'wpmcp_protected', "Item $member is protected: link its translations in wp-admin.", 403 );
		}
	}
	pll_save_post_translations( $merged );
	foreach ( $merged as $member ) {
		clean_post_cache( $member );
		if ( 'publish' === get_post_status( $member ) ) {
			wpmcp_live_record( 'linked translations', $member );
		}
	}
	return array( 'translations' => wpmcp_post_translations( (int) reset( $merged ) ) );
}

// ------------------------------------------------------------------ record and digest

/** Remembers a live change for the hourly digest: action, ID, time; never content. */
function wpmcp_live_record( string $action, int $id ): void {
	$log   = get_option( WPMCP_BRIDGE_LOG_OPTION, array() );
	$log   = is_array( $log ) ? $log : array();
	$log[] = array( 'time' => time(), 'action' => $action, 'id' => $id );
	update_option( WPMCP_BRIDGE_LOG_OPTION, array_slice( $log, -WPMCP_BRIDGE_LOG_MAX ), false );
	if ( ! wp_next_scheduled( WPMCP_BRIDGE_DIGEST_EVENT ) ) {
		wp_schedule_single_event( time() + HOUR_IN_SECONDS, WPMCP_BRIDGE_DIGEST_EVENT );
	}
}

add_action( WPMCP_BRIDGE_DIGEST_EVENT, 'wpmcp_send_live_digest' );

/** One e-mail to the admin address with the live changes since the last one. */
function wpmcp_send_live_digest(): void {
	$log = get_option( WPMCP_BRIDGE_LOG_OPTION, array() );
	if ( ! is_array( $log ) || ! $log ) {
		return;
	}
	update_option( WPMCP_BRIDGE_LOG_OPTION, array(), false );
	$site  = wp_specialchars_decode( get_bloginfo( 'name' ), ENT_QUOTES );
	$lines = array();
	foreach ( $log as $entry ) {
		$lines[] = sprintf(
			'%s  %s  #%d  %s',
			gmdate( 'Y-m-d H:i', (int) $entry['time'] ) . ' UTC',
			$entry['action'],
			(int) $entry['id'],
			admin_url( 'post.php?post=' . (int) $entry['id'] . '&action=edit' )
		);
	}
	$body = "Changes made through wpmcp (the AI assistant connection) on $site:\n\n" . implode( "\n", $lines )
		. "\n\nEach one can be undone in wp-admin (revisions, trash). To stop all access: wpmcp-admin revoke-all on the server.\n";
	wp_mail( get_option( 'admin_email' ), "[$site] " . count( $log ) . ' change(s) by wpmcp', $body );
}
