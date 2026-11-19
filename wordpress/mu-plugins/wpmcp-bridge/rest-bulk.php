<?php
/**
 * Bulk changes of wpmcp/v1: one change applied to up to 50 posts and pages in one call,
 * without the caller reading or sending their bodies. Two kinds: add a block with a link
 * to one page (skipping items that already link to it), and set SEO fields (only those
 * that differ). Both are "ensure" operations: an item that already is as asked is left
 * alone, not written again, so repeating a call changes nothing more.
 *
 * Every item is checked as a single change would be (its version, the editor lock,
 * protected items, its status, what kses would strip) and refused on its own; the others
 * go on. dry_run answers what would happen and writes nothing. A run that changes
 * something is a batch: it is kept (the last 10) so undo-bulk-change can put back every
 * item nobody has changed since, and it goes into the owner's digest as one entry with
 * the IDs it changed. Link changes keep a revision of each item first, as live edits do.
 */

defined( 'ABSPATH' ) || exit;

const WPMCP_BRIDGE_BULK_MAX    = 50;
const WPMCP_BRIDGE_BATCHES     = 'wpmcp_batches';
const WPMCP_BRIDGE_BATCH_KEEP  = 10;
// The closing tags that may end a body wrapped in an element (<article><div>...): a link
// block "before_closing_tags" goes inside them. Not </section>: that would put it inside
// the body's last section.
const WPMCP_BRIDGE_WRAPPER_END = '#(?:\s*</(?:div|article|main)\s*>)+\s*$#i';

/** The bulk operations: routes of wpmcp/v1 and abilities (see abilities.php). */
function wpmcp_bulk_operations(): array {
	$items = static fn ( array $extra ) => array(
		'type'     => 'array',
		'required' => true,
		'minItems' => 1,
		'maxItems' => WPMCP_BRIDGE_BULK_MAX,
		'items'    => array(
			'type'                 => 'object',
			'additionalProperties' => false,
			'required'             => array_merge( array( 'id', 'expected_version' ), array_keys( $extra ) ),
			'properties'           => array(
				'id'               => array( 'type' => 'integer', 'minimum' => 1 ),
				'expected_version' => array( 'type' => 'string', 'pattern' => '^[0-9a-f]{16}$' ),
			) + $extra,
		),
	);
	$dry_run = array( 'type' => 'boolean', 'default' => false );

	return array(
		array(
			'ability'             => 'wpmcp/bulk-add-links',
			'label'               => 'Add a link to many items',
			'description'         => 'Add the same block, with a link to one published post or page, to up to 50 posts or pages, except those that already link to it. Their bodies are neither read nor sent; each item is checked against its version, and the batch can be undone.',
			'route'               => '/bulk/links',
			'methods'             => 'POST',
			'callback'            => 'wpmcp_rest_bulk_links',
			'permission_callback' => 'wpmcp_can_read',
			'annotations'         => wpmcp_annotations( false, true, true ),
			'args'                => array(
				'target_id' => array( 'type' => 'integer', 'minimum' => 1, 'required' => true ),
				'html'      => array( 'type' => 'string', 'minLength' => 1, 'maxLength' => 5000, 'required' => true ),
				'position'  => array( 'type' => 'string', 'enum' => array( 'end', 'before_closing_tags' ), 'default' => 'end' ),
				'items'     => $items( array() ),
				'dry_run'   => $dry_run,
			),
		),
		array(
			'ability'             => 'wpmcp/bulk-update-seo',
			'label'               => 'Set SEO fields of many items',
			'description'         => 'Set SEO fields (All in One SEO or SEOPress) of up to 50 posts or pages; only the fields given that differ are written, and items already as asked are left alone. Each item is checked against its version, and the batch can be undone.',
			'route'               => '/bulk/seo',
			'methods'             => 'POST',
			'callback'            => 'wpmcp_rest_bulk_seo',
			'permission_callback' => 'wpmcp_can_read',
			'annotations'         => wpmcp_annotations( false, true, true ),
			'args'                => array(
				'items'   => $items( array( 'seo' => wpmcp_content_field_args()['seo'] ) ),
				'dry_run' => $dry_run,
			),
		),
		array(
			'ability'             => 'wpmcp/undo-bulk-change',
			'label'               => 'Undo a bulk change',
			'description'         => 'Put back the items of a batch of bulk-add-links or bulk-update-seo that have not changed since; the others are listed and stay as they are.',
			'route'               => '/bulk/(?P<batch_id>[0-9a-f]{12})/undo',
			'methods'             => 'POST',
			'callback'            => 'wpmcp_rest_bulk_undo',
			'permission_callback' => 'wpmcp_can_read',
			'annotations'         => wpmcp_annotations( false, true, true ),
			'args'                => array(
				'batch_id' => array( 'type' => 'string', 'pattern' => '^[0-9a-f]{12}$', 'required' => true ),
			),
		),
	);
}

// ---------------------------------------------------------------------------- the run

/**
 * Runs $plan on each item and applies what it returns. $plan( $post, $item ) answers null
 * when the item already is as asked, a WP_Error to refuse it, or array( 'apply' => fn )
 * where apply() makes the change and returns what undoing it needs (or a WP_Error).
 */
function wpmcp_bulk_run( WP_REST_Request $request, string $kind, string $action, callable $plan, array $notes = array() ): array {
	$dry_run   = (bool) $request['dry_run'];
	$changed   = array();
	$unchanged = array();
	$refused   = array();
	$undo      = array();
	$live      = array();
	$refuse    = static function ( int $id, WP_Error $error ) use ( &$refused ) {
		$refused[] = array( 'id' => $id, 'code' => $error->get_error_code(), 'reason' => $error->get_error_message() );
	};

	foreach ( (array) $request['items'] as $item ) {
		$id   = (int) $item['id'];
		$post = get_post( $id );
		if ( isset( $unchanged[ $id ] ) || isset( $changed[ $id ] ) || in_array( $id, array_column( $refused, 'id' ), true ) ) {
			$refuse( $id, wpmcp_error( 'wpmcp_duplicate', "Item $id is listed more than once." ) );
			continue;
		}
		if ( ! $post || ! in_array( $post->post_type, WPMCP_BRIDGE_TYPES, true ) || ! current_user_can( 'edit_post', $id ) ) {
			$refuse( $id, wpmcp_error( 'wpmcp_not_found', "No post or page with ID $id.", 404 ) );
			continue;
		}
		$is_live = in_array( $post->post_status, WPMCP_BRIDGE_LIVE, true );
		$step    = wpmcp_check_status( $post, array_merge( WPMCP_BRIDGE_EDITABLE, WPMCP_BRIDGE_LIVE ), 'restore it from the trash first' )
			?? $plan( $post, $item );
		if ( null === $step ) {
			// Already as asked: no version needed, nothing written (a repeat lands here).
			$unchanged[ $id ] = $id;
			continue;
		}
		$step = is_wp_error( $step ) ? $step : ( ( $is_live ? wpmcp_check_protected( $post ) : null ) ?? wpmcp_bulk_guard( $post, (string) $item['expected_version'] ) ?? $step );
		if ( is_wp_error( $step ) ) {
			$refuse( $id, $step );
			continue;
		}
		if ( $dry_run ) {
			$changed[ $id ] = $id;
			continue;
		}
		$done = call_user_func( $step['apply'] );
		if ( is_wp_error( $done ) ) {
			$refuse( $id, $done );
			continue;
		}
		clean_post_cache( $id );
		$version        = wpmcp_content_version( get_post( $id ), wpmcp_seo( $id ) );
		$changed[ $id ] = array( 'id' => $id, 'version' => $version );
		$undo[]         = array( 'id' => $id, 'version' => $version ) + $done;
		if ( $is_live ) {
			$live[] = $id;
		}
	}

	$batch_id = null;
	if ( $undo ) {
		$batch_id = wpmcp_batch_save( $kind, $undo );
		if ( $live ) {
			wpmcp_live_record( "$action (batch $batch_id)", 0, $live );
		}
		$notes[] = "undo_bulk_change with batch_id $batch_id puts these items back while nobody changes them.";
	}
	$result = array(
		'dry_run'  => $dry_run,
		'batch_id' => $batch_id,
		'counts'   => array(
			( $dry_run ? 'would_change' : 'changed' ) => count( $changed ),
			'unchanged'                               => count( $unchanged ),
			'refused'                                 => count( $refused ),
		),
		// Real runs answer the new versions, so a further change needs no read.
		( $dry_run ? 'would_change' : 'changed' ) => array_values( $changed ),
		'unchanged' => array_values( $unchanged ),
		'refused'   => $refused,
	);
	if ( $notes ) {
		$result['notes'] = $notes;
	}
	return $result;
}

/** wpmcp_edit_guard for one item of a bulk change (synchronisation is checked once). */
function wpmcp_bulk_guard( WP_Post $post, string $expected_version ): ?WP_Error {
	$request = new WP_REST_Request( 'POST' );
	$request->set_param( 'expected_version', $expected_version );
	$stale = wpmcp_edit_guard( $post, $request );
	if ( $stale && 'wpmcp_stale' === $stale->get_error_code() ) {
		// The single-item message says to read it again with get_content; a version from
		// search_content is enough here.
		return wpmcp_error( 'wpmcp_stale', "Item {$post->ID} changed since that version was read: take its current version from search_content and try again.", 409 );
	}
	return $stale;
}

// ---------------------------------------------------------------------------- links

function wpmcp_rest_bulk_links( WP_REST_Request $request ) {
	$blocked = wpmcp_writes_blocked();
	if ( $blocked ) {
		return $blocked;
	}
	$target = get_post( (int) $request['target_id'] );
	if ( ! $target || ! in_array( $target->post_type, WPMCP_BRIDGE_TYPES, true ) || 'publish' !== $target->post_status ) {
		return wpmcp_error( 'wpmcp_bad_target', 'target_id must be a published post or page.' );
	}
	$html  = trim( (string) $request['html'] );
	$clean = trim( wp_kses_post( $html ) );
	if ( ! wpmcp_links_to( $clean, $target ) ) {
		return wpmcp_error( 'wpmcp_no_link', 'html must contain a link (<a href="...">) to the target, whose address is ' . get_permalink( $target ) . '.' );
	}
	$notes = array();
	if ( wpmcp_kses_losses( $html ) ) {
		$notes[] = 'Markup this account may not use was removed from html: ' . implode( ', ', wpmcp_kses_losses( $html ) ) . '.';
	}
	$position    = (string) $request['position'];
	$target_lang = wpmcp_post_lang( $target->ID );

	$plan = static function ( WP_Post $post ) use ( $target, $clean, $position, $target_lang ) {
		if ( (int) $post->ID === (int) $target->ID ) {
			return wpmcp_error( 'wpmcp_self_link', "Item {$post->ID} is the target itself." );
		}
		if ( wpmcp_links_to( $post->post_content, $target ) ) {
			return null;
		}
		$lang = wpmcp_post_lang( $post->ID );
		if ( $lang && $target_lang && $lang !== $target_lang ) {
			$other = wpmcp_post_translations( $target->ID )[ $lang ] ?? null;
			$hint  = $other ? "link to its $lang version, $other, in a call of its own" : "the target has no $lang version";
			return wpmcp_error( 'wpmcp_lang_mismatch', "Item {$post->ID} is in $lang and the target in $target_lang: $hint." );
		}
		$content = wpmcp_add_html( $post->post_content, $clean, $position );
		$losses  = wpmcp_kses_losses( $content );
		if ( $losses ) {
			return wpmcp_error( 'wpmcp_markup_lost', "Saving item {$post->ID} would remove " . implode( ', ', $losses ) . ' from its body (markup only an administrator may save): add the link in wp-admin.', 409 );
		}
		return array(
			'apply' => static function () use ( $post, $content ) {
				$revision = wpmcp_keep_revision( $post );
				$result   = wp_update_post( wp_slash( array( 'ID' => $post->ID, 'post_content' => $content ) ), true );
				if ( is_wp_error( $result ) ) {
					return wpmcp_error( 'wpmcp_update_failed', $result->get_error_message(), 500 );
				}
				return array( 'revision_id' => $revision );
			},
		);
	};
	return wpmcp_bulk_run( $request, 'links', "added a link to #{$target->ID}", $plan, $notes );
}

/**
 * $content with $html added: at the end (as a Custom HTML block when the body is block
 * markup), or before_closing_tags: before the closing </div>, </article> or </main> tags
 * that end a body wrapped in an element, else at the end as well.
 */
function wpmcp_add_html( string $content, string $html, string $position ): string {
	if ( 'before_closing_tags' === $position && preg_match( WPMCP_BRIDGE_WRAPPER_END, $content, $end, PREG_OFFSET_CAPTURE ) ) {
		$at = (int) $end[0][1];
		return substr( $content, 0, $at ) . $html . substr( $content, $at );
	}
	$block = has_blocks( $content ) ? get_comment_delimited_block_content( 'core/html', array(), $html ) : $html;
	return '' === trim( $content ) ? $block : rtrim( $content ) . "\n\n" . $block;
}

// ------------------------------------------------------------------------------ SEO

function wpmcp_rest_bulk_seo( WP_REST_Request $request ) {
	$blocked = wpmcp_writes_blocked();
	if ( $blocked ) {
		return $blocked;
	}
	$plugin = wpmcp_seo_writable();
	if ( is_wp_error( $plugin ) ) {
		return $plugin;
	}
	$plan = static function ( WP_Post $post, array $item ) {
		$wanted = (array) $item['seo'];
		$check  = wpmcp_check_text( wpmcp_seo_texts( $wanted ) );
		if ( $check ) {
			return $check;
		}
		$current = (array) wpmcp_seo( $post->ID );
		$change  = array();
		$before  = array();
		foreach ( $wanted as $key => $value ) {
			$new = null === $value ? '' : sanitize_text_field( (string) $value );
			$new = '' === $new ? null : $new;
			$old = $current[ $key ] ?? null;
			if ( $new !== $old ) {
				$change[ $key ] = $new;
				$before[ $key ] = $old;
			}
		}
		if ( ! $change ) {
			return null;
		}
		return array(
			'apply' => static function () use ( $post, $change, $before ) {
				$error = wpmcp_save_seo( $post->ID, $change );
				return $error ?? array( 'seo' => $before );
			},
		);
	};
	return wpmcp_bulk_run( $request, 'seo', 'updated SEO fields', $plan );
}

// ---------------------------------------------------------------------------- batches

function wpmcp_batches(): array {
	$batches = get_option( WPMCP_BRIDGE_BATCHES, array() );
	return is_array( $batches ) ? $batches : array();
}

/** Keeps a batch (the last WPMCP_BRIDGE_BATCH_KEEP) and returns its ID. */
function wpmcp_batch_save( string $kind, array $items ): string {
	$id                  = bin2hex( random_bytes( 6 ) );
	$batches             = wpmcp_batches();
	$batches[ "b$id" ]   = array( 'kind' => $kind, 'time' => time(), 'items' => $items );
	update_option( WPMCP_BRIDGE_BATCHES, array_slice( $batches, -WPMCP_BRIDGE_BATCH_KEEP, null, true ), false );
	return $id;
}

/**
 * Puts back each item of a batch still at the version the batch left it: the revision
 * kept before a link was added, or the SEO fields as they were. Items changed since are
 * skipped (undo them by hand: list_revisions, restore_revision); items undone leave the
 * batch, so a repeat retries only what was skipped.
 */
function wpmcp_rest_bulk_undo( WP_REST_Request $request ) {
	$blocked = wpmcp_writes_blocked();
	if ( $blocked ) {
		return $blocked;
	}
	$key     = 'b' . (string) $request['batch_id'];
	$batches = wpmcp_batches();
	$batch   = $batches[ $key ] ?? null;
	if ( ! $batch ) {
		return wpmcp_error( 'wpmcp_no_batch', "There is no batch {$request['batch_id']} (the last " . WPMCP_BRIDGE_BATCH_KEEP . ' are kept).', 404 );
	}
	if ( ! $batch['items'] ) {
		return wpmcp_error( 'wpmcp_batch_undone', "Batch {$request['batch_id']} was already undone.", 409 );
	}

	$undone  = array();
	$skipped = array();
	$left    = array();
	$live    = array();
	foreach ( $batch['items'] as $entry ) {
		$id      = (int) $entry['id'];
		$post    = get_post( $id );
		$skip    = null;
		$is_live = false;
		if ( ! $post || ! current_user_can( 'edit_post', $id ) || 'trash' === $post->post_status ) {
			$skip = "Item $id is gone or in the trash.";
		} elseif ( ! hash_equals( wpmcp_content_version( $post, wpmcp_seo( $id ) ), (string) $entry['version'] ) ) {
			$skip = "Item $id changed after the batch: undo it by hand (list_revisions, restore_revision, or its SEO fields).";
		} else {
			$is_live = in_array( $post->post_status, WPMCP_BRIDGE_LIVE, true );
			$check   = ( $is_live ? wpmcp_check_protected( $post ) : null ) ?? wpmcp_bulk_guard( $post, (string) $entry['version'] );
			if ( $check ) {
				$skip = $check->get_error_message();
			} elseif ( 'links' === $batch['kind'] ) {
				$skip = empty( $entry['revision_id'] ) || ! wp_restore_post_revision( (int) $entry['revision_id'] )
					? "Item $id: the revision kept before the change could not be restored."
					: null;
			} else {
				$error = wpmcp_save_seo( $id, (array) $entry['seo'] );
				$skip  = $error ? "Item $id: " . $error->get_error_message() : null;
			}
		}
		if ( null === $skip ) {
			clean_post_cache( $id );
			$undone[] = $id;
			if ( $is_live ) {
				$live[] = $id;
			}
		} else {
			$skipped[] = array( 'id' => $id, 'reason' => $skip );
			$left[]    = $entry;
		}
	}
	$batches[ $key ]['items'] = $left;
	update_option( WPMCP_BRIDGE_BATCHES, $batches, false );
	if ( $live ) {
		wpmcp_live_record( "undid batch {$request['batch_id']}", 0, $live );
	}
	return array(
		'batch_id' => (string) $request['batch_id'],
		'counts'   => array( 'undone' => count( $undone ), 'skipped' => count( $skipped ) ),
		'undone'   => $undone,
		'skipped'  => $skipped,
	);
}
