<?php
/**
 * Write routes of wpmcp/v1: drafts, categories and images.
 *
 * Nothing here changes what visitors see. New posts and pages are always drafts; only
 * drafts and pending items can be edited; a draft linked as a translation stays out of
 * the language switcher and hreflang until it is published (Polylang lists only the
 * translations a visitor can read). Publishing and live edits are in rest-live.php.
 *
 * Images are shared by every language (Polylang media support is off), so editing an
 * image's alt text or caption shows wherever it is used.
 *
 * The site's core tables are utf8mb3: WordPress turns emoji in post titles, excerpts and
 * content into HTML entities, but post meta (alt text, SEO copies) and term names cannot
 * hold 4-byte characters, so those are refused with a clear message instead of being
 * silently dropped.
 */

defined( 'ABSPATH' ) || exit;

const WPMCP_BRIDGE_EDITABLE    = array( 'draft', 'pending' );
const WPMCP_BRIDGE_IMAGE_MIMES = array(
	'jpg|jpeg|jpe' => 'image/jpeg',
	'png'          => 'image/png',
	'webp'         => 'image/webp',
);

add_action( 'rest_api_init', 'wpmcp_register_write_routes' );

/** The editable fields of a post or page, as REST argument schemas. */
function wpmcp_content_field_args(): array {
	$text     = static fn ( int $max ) => array( 'type' => 'string', 'maxLength' => $max );
	$nullable = static fn ( int $max ) => array( 'type' => array( 'string', 'null' ), 'maxLength' => $max );
	return array(
		'title'          => $text( 500 ),
		'content'        => $text( 1000000 ),
		'excerpt'        => $text( 5000 ),
		'slug'           => $text( 200 ),
		'categories'     => array(
			'type'        => 'array',
			'items'       => array( 'type' => 'integer', 'minimum' => 1 ),
			'maxItems'    => 20,
			'uniqueItems' => true,
		),
		'featured_media' => array( 'type' => 'integer', 'minimum' => 0 ),
		'parent'         => array( 'type' => 'integer', 'minimum' => 0 ),
		'seo'            => array(
			'type'                 => 'object',
			'additionalProperties' => false,
			'properties'           => array(
				'title'           => $nullable( 300 ),
				'description'     => $nullable( 1000 ),
				'focus_keyphrase' => $nullable( 200 ),
				'og_title'        => $nullable( 300 ),
				'og_description'  => $nullable( 1000 ),
			),
		),
	);
}

function wpmcp_register_write_routes(): void {
	$text      = static fn ( int $max ) => array( 'type' => 'string', 'maxLength' => $max );
	$id        = array( 'type' => 'integer', 'minimum' => 1, 'required' => true );
	$lang      = array( 'type' => 'string', 'pattern' => '^[a-z]{2,3}$', 'required' => true );
	$reference = array( 'type' => 'integer', 'minimum' => 1 );
	$fields    = wpmcp_content_field_args();

	register_rest_route(
		WPMCP_BRIDGE_NS,
		'/content',
		array(
			'methods'             => 'POST',
			'callback'            => 'wpmcp_rest_content_create',
			'permission_callback' => 'wpmcp_can_create',
			'args'                => $fields + array(
				'type'           => array( 'type' => 'string', 'enum' => WPMCP_BRIDGE_TYPES, 'required' => true ),
				'lang'           => $lang,
				'translation_of' => $reference,
			),
		)
	);
	register_rest_route(
		WPMCP_BRIDGE_NS,
		'/content/(?P<id>\d+)',
		array(
			'methods'             => 'POST',
			'callback'            => 'wpmcp_rest_content_update',
			'permission_callback' => 'wpmcp_can_edit_item',
			'args'                => $fields + array(
				'id'                    => $id,
				'expected_version' => array( 'type' => 'string', 'pattern' => '^[0-9a-f]{16}$', 'required' => true ),
			),
		)
	);
	register_rest_route(
		WPMCP_BRIDGE_NS,
		'/terms',
		array(
			'methods'             => 'POST',
			'callback'            => 'wpmcp_rest_term_create',
			'permission_callback' => static fn () => current_user_can( 'manage_categories' ),
			'args'                => array(
				'name'           => $text( 200 ) + array( 'required' => true ),
				'lang'           => $lang,
				'parent'         => array( 'type' => 'integer', 'minimum' => 0 ),
				'description'    => $text( 2000 ),
				'translation_of' => $reference,
			),
		)
	);
	$media = array(
		'title'   => $text( 200 ),
		'alt'     => $text( 500 ),
		'caption' => $text( 2000 ),
	);
	register_rest_route(
		WPMCP_BRIDGE_NS,
		'/media',
		array(
			'methods'             => 'POST',
			'callback'            => 'wpmcp_rest_media_upload',
			'permission_callback' => static fn () => current_user_can( 'upload_files' ),
			'args'                => $media + array( 'attach_to' => array( 'type' => 'integer', 'minimum' => 0 ) ),
		)
	);
	register_rest_route(
		WPMCP_BRIDGE_NS,
		'/media/(?P<id>\d+)',
		array(
			'methods'             => 'POST',
			'callback'            => 'wpmcp_rest_media_update',
			'permission_callback' => 'wpmcp_can_edit_media',
			'args'                => $media + array( 'id' => $id ),
		)
	);
}

// ------------------------------------------------------------------------ permissions

function wpmcp_can_create( WP_REST_Request $request ): bool {
	return current_user_can( 'page' === $request['type'] ? 'edit_pages' : 'edit_posts' );
}

function wpmcp_can_edit_media( WP_REST_Request $request ) {
	$attachment = get_post( (int) $request['id'] );
	if ( ! $attachment || 'attachment' !== $attachment->post_type || ! wp_attachment_is_image( $attachment ) ) {
		return wpmcp_error( 'wpmcp_not_found', 'No image with that ID (see list_media).', 404 );
	}
	return current_user_can( 'edit_post', $attachment->ID );
}

// ---------------------------------------------------------------------------- checks

/**
 * With Polylang synchronisation on, saving one translation copies categories, meta or
 * dates to the others, published ones included: writes are refused until it is off.
 */
function wpmcp_writes_blocked(): ?WP_Error {
	if ( function_exists( 'PLL' ) && ! empty( PLL()->options['sync'] ) ) {
		return wpmcp_error(
			'wpmcp_sync_enabled',
			'Polylang synchronisation is enabled, so saving a draft could change its published translations. The site owner must turn it off (Languages > Settings > Synchronisation) before content can be written.',
			409
		);
	}
	return null;
}

/** Refuses 4-byte characters (emoji) in fields stored in utf8mb3 columns. */
function wpmcp_check_text( array $values ): ?WP_Error {
	foreach ( $values as $name => $value ) {
		if ( is_string( $value ) && preg_match( '/[\x{10000}-\x{10FFFF}]/u', $value ) ) {
			return wpmcp_error(
				'wpmcp_emoji',
				"$name cannot contain emoji or other 4-byte characters on this site (its database keeps them only in titles, excerpts and content)."
			);
		}
	}
	return null;
}

function wpmcp_seo_texts( array $seo ): array {
	$texts = array();
	foreach ( $seo as $key => $value ) {
		$texts[ "seo.$key" ] = $value;
	}
	return $texts;
}

/** Category IDs for a post in $lang: each must exist and be in that language. */
function wpmcp_categories_for( array $ids, string $lang ) {
	$valid = array();
	foreach ( $ids as $id ) {
		$term = get_term( (int) $id, 'category' );
		if ( ! $term instanceof WP_Term ) {
			return wpmcp_error( 'wpmcp_bad_category', "No category with ID $id (see list_terms)." );
		}
		$term_lang = wpmcp_term_lang( $term->term_id );
		if ( $term_lang && $term_lang !== $lang ) {
			$translated = function_exists( 'pll_get_term' ) ? (int) pll_get_term( $term->term_id, $lang ) : 0;
			$hint       = $translated ? "Its $lang version is $translated." : "It has no $lang version yet (create_category with translation_of=$id).";
			return wpmcp_error( 'wpmcp_category_lang', "Category $id is in $term_lang, not $lang. $hint" );
		}
		$valid[] = (int) $term->term_id;
	}
	return $valid;
}

/** The source post's categories in $lang, plus notes for those without a translation. */
function wpmcp_translated_categories( int $source_id, string $lang ): array {
	$ids   = array();
	$notes = array();
	foreach ( wpmcp_terms_of( $source_id, 'category' ) as $term ) {
		$translated = function_exists( 'pll_get_term' ) ? (int) pll_get_term( $term['id'], $lang ) : 0;
		if ( $translated ) {
			$ids[] = $translated;
		} else {
			$notes[] = sprintf( 'Category "%s" (%d) has no %s version, so it was not assigned.', $term['name'], $term['id'], $lang );
		}
	}
	return array( $ids, $notes );
}

function wpmcp_check_image( int $id ): ?WP_Error {
	$attachment = get_post( $id );
	if ( ! $attachment || 'attachment' !== $attachment->post_type || ! wp_attachment_is_image( $attachment ) ) {
		return wpmcp_error( 'wpmcp_bad_image', "No image with ID $id (see list_media)." );
	}
	return null;
}

function wpmcp_check_parent( int $parent_id, string $type, string $lang, int $self = 0 ): ?WP_Error {
	if ( 'page' !== $type ) {
		return wpmcp_error( 'wpmcp_bad_parent', 'Only pages have a parent.' );
	}
	$parent = get_post( $parent_id );
	if ( ! $parent || 'page' !== $parent->post_type || $parent_id === $self || in_array( $self, get_post_ancestors( $parent ), true ) ) {
		return wpmcp_error( 'wpmcp_bad_parent', "Page $parent_id cannot be the parent here." );
	}
	$parent_lang = wpmcp_post_lang( $parent_id );
	if ( $parent_lang && $parent_lang !== $lang ) {
		return wpmcp_error( 'wpmcp_bad_parent', "Parent page $parent_id is in $parent_lang, not $lang." );
	}
	return null;
}

/**
 * Who a new item is attributed to (the byline): the source's author for a translation,
 * WPMCP_BRIDGE_AUTHOR_LOGIN if defined, else the author of the latest published item. Never
 * the integration user unless nobody else exists.
 */
function wpmcp_default_author( string $type, ?WP_Post $source ): int {
	if ( $source ) {
		return (int) $source->post_author;
	}
	if ( defined( 'WPMCP_BRIDGE_AUTHOR_LOGIN' ) ) {
		$user = get_user_by( 'login', (string) WPMCP_BRIDGE_AUTHOR_LOGIN );
		if ( $user ) {
			return (int) $user->ID;
		}
	}
	$latest = get_posts(
		array(
			'post_type'      => $type,
			'post_status'    => 'publish',
			'numberposts'    => 1,
			'lang'           => '',
			'author__not_in' => array( wpmcp_user_id() ),
		)
	);
	return $latest ? (int) $latest[0]->post_author : get_current_user_id();
}

/** True when WordPress stored something other than what was sent (kses, normalisation). */
function wpmcp_content_filtered( string $sent, string $stored ): bool {
	$normalise = static fn ( string $html ) => trim( str_replace( "\r\n", "\n", wp_encode_emoji( $html ) ) );
	return $normalise( $sent ) !== $normalise( $stored );
}

// ------------------------------------------------------------------------------- SEO

/** Writes only the given All in One SEO fields (AIOSEO 5 saves patches, not whole rows). */
function wpmcp_save_seo( int $post_id, array $seo ): ?WP_Error {
	if ( ! $seo ) {
		return null;
	}
	$model = '\\AIOSEO\\Plugin\\Common\\Models\\Post';
	if ( ! class_exists( $model ) ) {
		return wpmcp_error( 'wpmcp_no_seo', 'All in One SEO is not active: the SEO fields were not saved.' );
	}
	$data = array();
	foreach ( array( 'title', 'description', 'og_title', 'og_description' ) as $key ) {
		if ( array_key_exists( $key, $seo ) ) {
			$data[ $key ] = null === $seo[ $key ] ? '' : sanitize_text_field( $seo[ $key ] );
		}
	}
	if ( array_key_exists( 'focus_keyphrase', $seo ) ) {
		$focus      = null === $seo['focus_keyphrase'] ? '' : sanitize_text_field( $seo['focus_keyphrase'] );
		$row        = $model::getPost( $post_id );
		$keyphrases = $row->exists() && ! empty( $row->keyphrases ) ? $row->keyphrases : null;
		if ( is_string( $keyphrases ) ) {
			$keyphrases = json_decode( $keyphrases );
		}
		// Keep the additional keyphrases; replace only the focus one.
		$keyphrases                   = $model::getKeyphrasesDefaults( is_object( $keyphrases ) ? $keyphrases : null );
		$keyphrases->focus->keyphrase = $focus;
		$data['keyphrases']           = json_decode( wp_json_encode( $keyphrases ), true );
		$data['focus_keyword']        = '' === $focus ? null : $focus;
	}
	$result = $model::savePost( $post_id, $data );
	if ( is_string( $result ) && '' !== $result ) {
		return wpmcp_error( 'wpmcp_seo_failed', 'All in One SEO could not save the SEO fields.', 500 );
	}
	return null;
}

// --------------------------------------------------------------------------- content

function wpmcp_write_result( int $id, array $notes = array(), ?string $sent_content = null ): array {
	clean_post_cache( $id );
	$post   = get_post( $id );
	$detail = wpmcp_content_detail( $post );
	// The caller has the body it sent; return its size, not the body again.
	$detail['content_length'] = mb_strlen( $post->post_content );
	unset( $detail['content'] );
	if ( null !== $sent_content && wpmcp_content_filtered( $sent_content, $post->post_content ) ) {
		$notes[] = 'WordPress changed the content while saving (markup this account may not use is removed). Read it with get_content to see what was kept.';
	}
	if ( $notes ) {
		$detail['notes'] = $notes;
	}
	return $detail;
}

function wpmcp_rest_content_create( WP_REST_Request $request ) {
	$blocked = wpmcp_writes_blocked();
	if ( $blocked ) {
		return $blocked;
	}
	$type = (string) $request['type'];
	$lang = wpmcp_lang_arg( (string) $request['lang'] );
	if ( is_wp_error( $lang ) ) {
		return $lang;
	}
	$title = trim( (string) $request['title'] );
	if ( '' === $title ) {
		return wpmcp_error( 'wpmcp_no_title', 'A title is required.' );
	}
	$seo   = (array) ( $request['seo'] ?? array() );
	$check = wpmcp_check_text( wpmcp_seo_texts( $seo ) );
	if ( $check ) {
		return $check;
	}

	$source = null;
	$group  = array();
	if ( $request['translation_of'] ) {
		$source = get_post( (int) $request['translation_of'] );
		if ( ! $source || $source->post_type !== $type || ! current_user_can( 'edit_post', $source->ID ) ) {
			return wpmcp_error( 'wpmcp_bad_source', "translation_of must be the ID of a $type." );
		}
		$source_lang = wpmcp_post_lang( $source->ID );
		if ( $source_lang === $lang ) {
			return wpmcp_error( 'wpmcp_same_lang', "Item {$source->ID} is already in $lang." );
		}
		$group = wpmcp_post_translations( $source->ID );
		if ( isset( $group[ $lang ] ) ) {
			return wpmcp_error( 'wpmcp_translation_exists', "Item {$source->ID} already has a $lang translation: {$group[ $lang ]}. Edit that one instead.", 409 );
		}
		if ( ! $group && $source_lang ) {
			$group = array( $source_lang => $source->ID );
		}
	}

	$notes      = array();
	$categories = null;
	if ( 'post' === $type && $request->has_param( 'categories' ) ) {
		$categories = wpmcp_categories_for( (array) $request['categories'], $lang );
		if ( is_wp_error( $categories ) ) {
			return $categories;
		}
	} elseif ( 'post' === $type && $source ) {
		list( $categories, $missing ) = wpmcp_translated_categories( $source->ID, $lang );
		$notes                        = array_merge( $notes, $missing );
	} elseif ( 'page' === $type && $request['categories'] ) {
		return wpmcp_error( 'wpmcp_bad_category', 'Pages have no categories.' );
	}

	$featured = $request->has_param( 'featured_media' ) ? (int) $request['featured_media'] : ( $source ? (int) get_post_thumbnail_id( $source ) : 0 );
	if ( $featured ) {
		$check = wpmcp_check_image( $featured );
		if ( $check ) {
			return $check;
		}
	}

	$parent = 0;
	if ( $request['parent'] ) {
		$check = wpmcp_check_parent( (int) $request['parent'], $type, $lang );
		if ( $check ) {
			return $check;
		}
		$parent = (int) $request['parent'];
	} elseif ( $source && $source->post_parent ) {
		$parent = function_exists( 'pll_get_post' ) ? (int) pll_get_post( $source->post_parent, $lang ) : 0;
		if ( ! $parent ) {
			$notes[] = "The parent page {$source->post_parent} has no $lang version, so this page has no parent.";
		}
	}

	$content = (string) $request['content'];
	$id      = wp_insert_post(
		wp_slash(
			array(
				'post_type'    => $type,
				'post_status'  => 'draft',
				'post_title'   => $title,
				'post_content' => $content,
				'post_excerpt' => (string) $request['excerpt'],
				'post_name'    => (string) $request['slug'],
				'post_parent'  => $parent,
				'post_author'  => wpmcp_default_author( $type, $source ),
				'menu_order'   => $source ? (int) $source->menu_order : 0,
			)
		),
		true
	);
	if ( is_wp_error( $id ) ) {
		return wpmcp_error( 'wpmcp_insert_failed', $id->get_error_message(), 500 );
	}
	if ( wpmcp_has_polylang() ) {
		pll_set_post_language( $id, $lang );
		if ( $source ) {
			pll_save_post_translations( $group + array( $lang => $id ) );
		}
	}
	if ( null !== $categories ) {
		wp_set_post_categories( $id, $categories );
	}
	if ( $featured ) {
		set_post_thumbnail( $id, $featured );
	}
	$seo_error = wpmcp_save_seo( $id, $seo );
	if ( $seo_error ) {
		$notes[] = $seo_error->get_error_message();
	}
	return wpmcp_write_result( $id, $notes, $content );
}

function wpmcp_rest_content_update( WP_REST_Request $request ) {
	$post = get_post( (int) $request['id'] );
	if ( ! in_array( $post->post_status, WPMCP_BRIDGE_EDITABLE, true ) ) {
		return wpmcp_error( 'wpmcp_not_draft', "Item {$post->ID} is {$post->post_status}: only drafts and pending items can be changed with this tool.", 409 );
	}
	$guard = wpmcp_edit_guard( $post, $request );
	if ( $guard ) {
		return $guard;
	}
	return wpmcp_apply_content_update( $post, $request );
}

/**
 * What every change to an existing item checks first: Polylang synchronisation off, the
 * version the caller read is still the current one, nobody has it open in the editor.
 */
function wpmcp_edit_guard( WP_Post $post, WP_REST_Request $request ): ?WP_Error {
	$blocked = wpmcp_writes_blocked();
	if ( $blocked ) {
		return $blocked;
	}
	if ( ! hash_equals( wpmcp_content_version( $post, wpmcp_seo( $post->ID ) ), (string) $request['expected_version'] ) ) {
		return wpmcp_error(
			'wpmcp_stale',
			"Item {$post->ID} changed since that version was read. Read it again with get_content and apply the change to the current version.",
			409
		);
	}
	if ( ! function_exists( 'wp_check_post_lock' ) ) {
		require_once ABSPATH . 'wp-admin/includes/post.php';
	}
	if ( wp_check_post_lock( $post->ID ) ) {
		return wpmcp_error( 'wpmcp_locked', "Someone has item {$post->ID} open in the editor. Try again when they have finished.", 409 );
	}
	return null;
}

/** The fields of update_draft (and update_published_content), after the guard. */
function wpmcp_apply_content_update( WP_Post $post, WP_REST_Request $request, array $notes = array() ) {
	$seo   = (array) ( $request['seo'] ?? array() );
	$check = wpmcp_check_text( wpmcp_seo_texts( $seo ) );
	if ( $check ) {
		return $check;
	}

	$lang    = (string) wpmcp_post_lang( $post->ID );
	$postarr = array( 'ID' => $post->ID );
	if ( $request->has_param( 'title' ) ) {
		$postarr['post_title'] = trim( (string) $request['title'] );
		if ( '' === $postarr['post_title'] ) {
			return wpmcp_error( 'wpmcp_no_title', 'The title cannot be empty.' );
		}
	}
	foreach ( array( 'content' => 'post_content', 'excerpt' => 'post_excerpt', 'slug' => 'post_name' ) as $param => $column ) {
		if ( $request->has_param( $param ) ) {
			$postarr[ $column ] = (string) $request[ $param ];
		}
	}
	if ( $request->has_param( 'parent' ) ) {
		$parent = (int) $request['parent'];
		if ( $parent ) {
			$check = wpmcp_check_parent( $parent, $post->post_type, $lang, $post->ID );
			if ( $check ) {
				return $check;
			}
		}
		$postarr['post_parent'] = $parent;
	}
	$categories = null;
	if ( $request->has_param( 'categories' ) ) {
		if ( 'post' !== $post->post_type ) {
			return wpmcp_error( 'wpmcp_bad_category', 'Pages have no categories.' );
		}
		$categories = wpmcp_categories_for( (array) $request['categories'], $lang );
		if ( is_wp_error( $categories ) ) {
			return $categories;
		}
	}
	$featured = $request->has_param( 'featured_media' ) ? (int) $request['featured_media'] : null;
	if ( $featured ) {
		$check = wpmcp_check_image( $featured );
		if ( $check ) {
			return $check;
		}
	}

	if ( count( $postarr ) > 1 ) {
		$result = wp_update_post( wp_slash( $postarr ), true );
		if ( is_wp_error( $result ) ) {
			return wpmcp_error( 'wpmcp_update_failed', $result->get_error_message(), 500 );
		}
	}
	if ( null !== $categories ) {
		wp_set_post_categories( $post->ID, $categories );
	}
	if ( null !== $featured ) {
		$featured ? set_post_thumbnail( $post->ID, $featured ) : delete_post_thumbnail( $post->ID );
	}
	$seo_error = wpmcp_save_seo( $post->ID, $seo );
	if ( $seo_error ) {
		$notes[] = $seo_error->get_error_message();
	}
	return wpmcp_write_result( $post->ID, $notes, $postarr['post_content'] ?? null );
}

// ------------------------------------------------------------------------ categories

function wpmcp_rest_term_create( WP_REST_Request $request ) {
	$lang = wpmcp_lang_arg( (string) $request['lang'] );
	if ( is_wp_error( $lang ) ) {
		return $lang;
	}
	$name        = trim( (string) $request['name'] );
	$description = (string) $request['description'];
	if ( '' === $name ) {
		return wpmcp_error( 'wpmcp_no_name', 'A name is required.' );
	}
	$check = wpmcp_check_text( array( 'name' => $name, 'description' => $description ) );
	if ( $check ) {
		return $check;
	}

	$args   = array( 'description' => $description );
	$notes  = array();
	$source = null;
	$group  = array();
	if ( $request['translation_of'] ) {
		$source = get_term( (int) $request['translation_of'], 'category' );
		if ( ! $source instanceof WP_Term ) {
			return wpmcp_error( 'wpmcp_bad_source', 'translation_of must be the ID of a category.' );
		}
		$source_lang = wpmcp_term_lang( $source->term_id );
		if ( $source_lang === $lang ) {
			return wpmcp_error( 'wpmcp_same_lang', "Category {$source->term_id} is already in $lang." );
		}
		$group = wpmcp_term_translations( $source->term_id );
		if ( isset( $group[ $lang ] ) ) {
			return wpmcp_error( 'wpmcp_translation_exists', "Category {$source->term_id} already has a $lang version: {$group[ $lang ]}.", 409 );
		}
		if ( ! $group && $source_lang ) {
			$group = array( $source_lang => $source->term_id );
		}
	}
	if ( $request['parent'] ) {
		$parent = get_term( (int) $request['parent'], 'category' );
		if ( ! $parent instanceof WP_Term ) {
			return wpmcp_error( 'wpmcp_bad_parent', "No category with ID {$request['parent']}." );
		}
		$parent_lang = wpmcp_term_lang( $parent->term_id );
		if ( $parent_lang && $parent_lang !== $lang ) {
			return wpmcp_error( 'wpmcp_bad_parent', "Parent category {$parent->term_id} is in $parent_lang, not $lang." );
		}
		$args['parent'] = $parent->term_id;
	} elseif ( $source && $source->parent ) {
		$parent = function_exists( 'pll_get_term' ) ? (int) pll_get_term( $source->parent, $lang ) : 0;
		if ( $parent ) {
			$args['parent'] = $parent;
		} else {
			$notes[] = "The parent category {$source->parent} has no $lang version, so this one is top-level.";
		}
	}

	$result = wp_insert_term( $name, 'category', $args );
	if ( is_wp_error( $result ) ) {
		$existing = $result->get_error_data( 'term_exists' );
		$message  = $existing ? "A category with that name already exists here (ID $existing). Use it, or choose another name." : $result->get_error_message();
		return wpmcp_error( 'wpmcp_term_failed', $message, $existing ? 409 : 400 );
	}
	$term_id = (int) $result['term_id'];
	if ( wpmcp_has_polylang() ) {
		pll_set_term_language( $term_id, $lang );
		if ( $source ) {
			pll_save_term_translations( $group + array( $lang => $term_id ) );
		}
	}
	clean_term_cache( $term_id, 'category' );
	$summary = wpmcp_term_summary( get_term( $term_id, 'category' ) );
	if ( $notes ) {
		$summary['notes'] = $notes;
	}
	return $summary;
}

// ---------------------------------------------------------------------------- images

function wpmcp_rest_media_upload( WP_REST_Request $request ) {
	$files = $request->get_file_params();
	if ( empty( $files['file'] ) ) {
		return wpmcp_error( 'wpmcp_no_file', 'No file was sent.' );
	}
	$check = wpmcp_check_text( array( 'alt' => (string) $request['alt'] ) );
	if ( $check ) {
		return $check;
	}
	$parent = (int) $request['attach_to'];
	if ( $parent ) {
		$target = get_post( $parent );
		if ( ! $target || ! in_array( $target->post_type, WPMCP_BRIDGE_TYPES, true ) || ! current_user_can( 'edit_post', $parent ) ) {
			return wpmcp_error( 'wpmcp_bad_parent', "No post or page with ID $parent to attach the image to." );
		}
	}
	require_once ABSPATH . 'wp-admin/includes/file.php';
	require_once ABSPATH . 'wp-admin/includes/media.php';
	require_once ABSPATH . 'wp-admin/includes/image.php';

	$post_data = array();
	if ( '' !== (string) $request['title'] ) {
		$post_data['post_title'] = sanitize_text_field( (string) $request['title'] );
	}
	if ( '' !== (string) $request['caption'] ) {
		$post_data['post_excerpt'] = sanitize_text_field( (string) $request['caption'] );
	}
	// WordPress checks the real type of the file again (wp_check_filetype_and_ext).
	$id = media_handle_upload( 'file', $parent, $post_data, array( 'test_form' => false, 'mimes' => WPMCP_BRIDGE_IMAGE_MIMES ) );
	if ( is_wp_error( $id ) ) {
		return wpmcp_error( 'wpmcp_upload_failed', $id->get_error_message() );
	}
	if ( '' !== (string) $request['alt'] ) {
		update_post_meta( $id, '_wp_attachment_image_alt', sanitize_text_field( (string) $request['alt'] ) );
	}
	return wpmcp_media_summary( get_post( $id ) );
}

function wpmcp_rest_media_update( WP_REST_Request $request ) {
	$id    = (int) $request['id'];
	$check = wpmcp_check_text( array( 'alt' => (string) $request['alt'] ) );
	if ( $check ) {
		return $check;
	}
	$postarr = array( 'ID' => $id );
	if ( $request->has_param( 'title' ) ) {
		$postarr['post_title'] = sanitize_text_field( (string) $request['title'] );
	}
	if ( $request->has_param( 'caption' ) ) {
		$postarr['post_excerpt'] = sanitize_text_field( (string) $request['caption'] );
	}
	if ( count( $postarr ) > 1 ) {
		$result = wp_update_post( wp_slash( $postarr ), true );
		if ( is_wp_error( $result ) ) {
			return wpmcp_error( 'wpmcp_update_failed', $result->get_error_message(), 500 );
		}
	}
	if ( $request->has_param( 'alt' ) ) {
		update_post_meta( $id, '_wp_attachment_image_alt', sanitize_text_field( (string) $request['alt'] ) );
	}
	clean_post_cache( $id );
	return wpmcp_media_summary( get_post( $id ) );
}
