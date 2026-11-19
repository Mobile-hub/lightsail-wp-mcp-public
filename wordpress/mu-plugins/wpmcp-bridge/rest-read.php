<?php
/**
 * Read routes of the wpmcp/v1 namespace. Core REST cannot filter by language or show
 * translation links with Polylang free, and does not expose SEO plugins' fields in a
 * usable shape; these routes do, read-only. Site-specific fields can be added to
 * get_content with the wpmcp_bridge_extra_fields filter (see helpers.php).
 *
 * Every route needs edit_posts; single items need edit_post on that item. Each one is also
 * an ability (wpmcp/get-content, ...): see abilities.php.
 */

defined( 'ABSPATH' ) || exit;

const WPMCP_BRIDGE_NS       = 'wpmcp/v1';
const WPMCP_BRIDGE_PER_PAGE = 50;
const WPMCP_BRIDGE_LINKS_MAX = 5000;

function wpmcp_can_read(): bool {
	return current_user_can( 'edit_posts' );
}

/** The read operations: routes of wpmcp/v1 and abilities (see abilities.php). */
function wpmcp_read_operations(): array {
	$read = array(
		'methods'             => WP_REST_Server::READABLE,
		'permission_callback' => 'wpmcp_can_read',
		'annotations'         => wpmcp_annotations( true, false, true ),
	);
	$item = array(
		'permission_callback' => 'wpmcp_can_edit_item',
		'args'                => array(
			'id' => array( 'type' => 'integer', 'minimum' => 1, 'required' => true ),
		),
	) + $read;
	$page = array(
		'page'     => array( 'type' => 'integer', 'minimum' => 1, 'default' => 1 ),
		'per_page' => array( 'type' => 'integer', 'minimum' => 1, 'maximum' => WPMCP_BRIDGE_PER_PAGE, 'default' => 20 ),
	);
	$lang = array( 'type' => 'string', 'default' => '', 'pattern' => '^[a-z]{0,3}$' );

	return array(
		array(
			'ability'     => 'wpmcp/get-site-overview',
			'label'       => 'Site overview',
			'description' => 'Name, URL, languages, post and page counts per status and language, front page, timezone and versions.',
			'route'       => '/overview',
			'callback'    => 'wpmcp_rest_overview',
		) + $read,
		array(
			'ability'     => 'wpmcp/search-content',
			'label'       => 'Search posts and pages',
			'description' => 'Posts and pages by words, language, status or category, or those still missing a translation: plain-text excerpts, languages, translation IDs and the version changes must name.',
			'route'       => '/content',
			'callback'    => 'wpmcp_rest_content_list',
			'args'        => $page + array(
				'type'            => array( 'type' => 'string', 'enum' => array( 'post', 'page', 'any' ), 'default' => 'any' ),
				'status'          => array( 'type' => 'string', 'enum' => array_merge( WPMCP_BRIDGE_STATUSES, array( 'any' ) ), 'default' => 'any' ),
				'lang'            => $lang,
				'search'          => array( 'type' => 'string', 'default' => '', 'maxLength' => 200 ),
				'category'        => array( 'type' => 'integer', 'minimum' => 0, 'default' => 0 ),
				'untranslated_to' => $lang,
				'orderby'         => array( 'type' => 'string', 'enum' => array( 'date', 'modified', 'title', 'relevance' ), 'default' => 'date' ),
				'order'           => array( 'type' => 'string', 'enum' => array( 'asc', 'desc' ), 'default' => 'desc' ),
			),
		) + $read,
		array(
			'ability'     => 'wpmcp/get-content',
			'label'       => 'Read a post or page',
			'description' => 'One post or page: body, excerpt, status, language and translations, categories, featured image, SEO fields, and the version that changes must name.',
			'route'       => '/content/(?P<id>\d+)',
			'callback'    => 'wpmcp_rest_content_get',
		) + $item,
		array(
			'ability'     => 'wpmcp/list-revisions',
			'label'       => 'List revisions',
			'description' => 'Saved revisions of a post or page, newest first, with dates and sizes.',
			'route'       => '/content/(?P<id>\d+)/revisions',
			'callback'    => 'wpmcp_rest_revisions',
		) + $item,
		array(
			'ability'     => 'wpmcp/list-terms',
			'label'       => 'List categories or tags',
			'description' => 'Categories or tags, with language, number of posts and translation links.',
			'route'       => '/terms',
			'callback'    => 'wpmcp_rest_terms',
			'args'        => array(
				'taxonomy' => array( 'type' => 'string', 'enum' => array( 'category', 'post_tag' ), 'default' => 'category' ),
				'lang'     => $lang,
				'search'   => array( 'type' => 'string', 'default' => '', 'maxLength' => 200 ),
			),
		) + $read,
		array(
			'ability'     => 'wpmcp/list-media',
			'label'       => 'List images',
			'description' => 'Images in the media library: title, alt text, caption, size and URL.',
			'route'       => '/media',
			'callback'    => 'wpmcp_rest_media',
			'args'        => $page + array(
				'search' => array( 'type' => 'string', 'default' => '', 'maxLength' => 200 ),
				'parent' => array( 'type' => 'integer', 'minimum' => 0, 'default' => 0 ),
			),
		) + $read,
		array(
			'ability'     => 'wpmcp/get-menus',
			'label'       => 'Show menus',
			'description' => 'Navigation menus shown by a theme location or a menu widget (a footer, a sidebar), per language, with their items and versions.',
			'route'       => '/menus',
			'callback'    => 'wpmcp_rest_menus', // in rest-menus.php
		) + $read,
		// Internal: the wpmcp service compares the public pages it fetches (inspect_page,
		// check_sitemap) with these. A route only, not an ability: no tool of its own.
		array(
			'ability'  => null,
			'route'    => '/links',
			'callback' => 'wpmcp_rest_links',
			'args'     => array(
				'ids' => array(
					'type'     => 'array',
					'items'    => array( 'type' => 'integer', 'minimum' => 1 ),
					'maxItems' => 100,
				),
			),
		) + $read,
		array(
			'ability'     => 'wpmcp/get-recent-changes',
			'label'       => 'Recent changes',
			'description' => 'Posts and pages created, edited, published or trashed recently, newest first.',
			'route'       => '/changes',
			'callback'    => 'wpmcp_rest_changes',
			'args'        => array(
				'since' => array( 'type' => 'string', 'default' => '', 'maxLength' => 40 ),
				'limit' => array( 'type' => 'integer', 'minimum' => 1, 'maximum' => WPMCP_BRIDGE_PER_PAGE, 'default' => 30 ),
			),
		) + $read,
	);
}

function wpmcp_can_edit_item( WP_REST_Request $request ) {
	$post = get_post( (int) $request['id'] );
	if ( ! $post || ! in_array( $post->post_type, WPMCP_BRIDGE_TYPES, true ) ) {
		return wpmcp_error( 'wpmcp_not_found', 'No post or page with that ID.', 404 );
	}
	return current_user_can( 'edit_post', $post->ID );
}

/** Validates a language slug against the site's languages; '' means all. */
function wpmcp_lang_arg( string $lang ) {
	if ( '' === $lang ) {
		return '';
	}
	if ( ! in_array( $lang, wpmcp_languages(), true ) ) {
		return wpmcp_error( 'wpmcp_bad_lang', 'Unknown language. Site languages: ' . implode( ', ', wpmcp_languages() ) . '.' );
	}
	return $lang;
}

function wpmcp_paged( WP_Query $query, array $items, int $page, int $per_page ): array {
	return array(
		'items'       => $items,
		'page'        => $page,
		'per_page'    => $per_page,
		'total'       => (int) $query->found_posts,
		'total_pages' => (int) $query->max_num_pages,
	);
}

function wpmcp_rest_overview(): array {
	$languages = array();
	if ( wpmcp_has_polylang() ) {
		$default = pll_default_language();
		foreach ( PLL()->model->get_languages_list() as $language ) {
			$languages[] = array(
				'slug'    => $language->slug,
				'name'    => $language->name,
				'locale'  => $language->locale,
				'default' => $language->slug === $default,
			);
		}
	}

	$counts = array();
	foreach ( WPMCP_BRIDGE_TYPES as $type ) {
		$by_status = array();
		foreach ( (array) wp_count_posts( $type ) as $status => $count ) {
			if ( in_array( $status, WPMCP_BRIDGE_STATUSES, true ) && (int) $count > 0 ) {
				$by_status[ $status ] = (int) $count;
			}
		}
		$by_lang = array();
		foreach ( wpmcp_languages() as $slug ) {
			$query            = new WP_Query( array( 'post_type' => $type, 'post_status' => 'publish', 'lang' => $slug, 'posts_per_page' => 1, 'fields' => 'ids' ) );
			$by_lang[ $slug ] = (int) $query->found_posts;
		}
		$counts[ $type ] = array( 'by_status' => $by_status, 'published_by_language' => $by_lang );
	}

	return array(
		'name'                 => html_entity_decode( get_bloginfo( 'name' ), ENT_QUOTES | ENT_HTML5, 'UTF-8' ),
		'tagline'              => html_entity_decode( get_bloginfo( 'description' ), ENT_QUOTES | ENT_HTML5, 'UTF-8' ),
		'url'                  => home_url( '/' ),
		'timezone'             => wp_timezone_string(),
		'server_time_gmt'      => gmdate( 'Y-m-d\TH:i:s\Z' ),
		'languages'            => $languages,
		// Not "content": ChatGPT shows a JSON result with a "content" key as {} to the model.
		'counts'               => $counts,
		'categories'           => (int) wp_count_terms( array( 'taxonomy' => 'category', 'hide_empty' => false ) ),
		'tags'                 => (int) wp_count_terms( array( 'taxonomy' => 'post_tag', 'hide_empty' => false ) ),
		'front_page'           => 'page' === get_option( 'show_on_front' ) ? (int) get_option( 'page_on_front' ) : 'latest posts',
		'permalink_structure'  => (string) get_option( 'permalink_structure' ),
		// The plugin that keeps the `seo` fields; two names (a+b) when both are active.
		'seo_plugin'           => implode( '+', wpmcp_seo_plugins() ) ?: null,
		'versions'             => array(
			'wordpress' => get_bloginfo( 'version' ),
			'polylang'  => defined( 'POLYLANG_VERSION' ) ? POLYLANG_VERSION : null,
			'aioseo'    => defined( 'AIOSEO_VERSION' ) ? AIOSEO_VERSION : null,
			'seopress'  => defined( 'SEOPRESS_VERSION' ) ? SEOPRESS_VERSION : null,
			'bridge'    => WPMCP_BRIDGE_VERSION,
		),
	);
}

function wpmcp_rest_content_list( WP_REST_Request $request ) {
	$lang = wpmcp_lang_arg( (string) $request['lang'] );
	if ( is_wp_error( $lang ) ) {
		return $lang;
	}
	$untranslated_to = wpmcp_lang_arg( (string) $request['untranslated_to'] );
	if ( is_wp_error( $untranslated_to ) ) {
		return $untranslated_to;
	}

	$type     = 'any' === $request['type'] ? WPMCP_BRIDGE_TYPES : array( (string) $request['type'] );
	$statuses = 'any' === $request['status'] ? array_diff( WPMCP_BRIDGE_STATUSES, array( 'trash' ) ) : array( (string) $request['status'] );
	$args     = array(
		'post_type'           => $type,
		'post_status'         => array_values( $statuses ),
		'lang'                => $lang,
		'paged'               => (int) $request['page'],
		'posts_per_page'      => (int) $request['per_page'],
		'order'               => strtoupper( (string) $request['order'] ),
		'orderby'             => (string) $request['orderby'],
		'ignore_sticky_posts' => true,
		'suppress_filters'    => false,
	);
	if ( '' !== $request['search'] ) {
		$args['s'] = (string) $request['search'];
	} elseif ( 'relevance' === $args['orderby'] ) {
		$args['orderby'] = 'date';
	}
	if ( $request['category'] ) {
		$args['cat'] = (int) $request['category'];
	}
	if ( '' !== $untranslated_to ) {
		// Items that have no translation in that language (and are not in it themselves).
		$covered = array();
		$targets = get_posts( array( 'post_type' => $type, 'post_status' => 'any', 'lang' => $untranslated_to, 'numberposts' => -1, 'fields' => 'ids' ) );
		foreach ( $targets as $target ) {
			foreach ( wpmcp_post_translations( (int) $target ) as $member ) {
				$covered[] = $member;
			}
		}
		$args['post__not_in'] = array_values( array_unique( array_merge( $covered, $targets ) ) );
	}

	$query = new WP_Query( $args );
	// With the version, a bulk change (rest-bulk.php) needs no read of each item first.
	$items = array_map(
		static fn ( WP_Post $post ) => array(
			'id'      => (int) $post->ID,
			'version' => wpmcp_content_version( $post, wpmcp_seo( $post->ID ) ),
		) + wpmcp_post_summary( $post ),
		$query->posts
	);
	return wpmcp_paged( $query, $items, (int) $request['page'], (int) $request['per_page'] );
}

/**
 * Public addresses of published posts and pages (those given, or all of them, at most
 * WPMCP_BRIDGE_LINKS_MAX): language, translations and whether SEO keeps it out of search.
 */
function wpmcp_rest_links( WP_REST_Request $request ): array {
	$ids  = array_values( array_unique( array_map( 'intval', (array) ( $request['ids'] ?? array() ) ) ) );
	$args = array(
		'post_type'   => WPMCP_BRIDGE_TYPES,
		'post_status' => 'publish',
		'lang'        => '',
		'numberposts' => $ids ? count( $ids ) : WPMCP_BRIDGE_LINKS_MAX,
		'orderby'     => 'ID',
		'order'       => 'ASC',
	);
	if ( $ids ) {
		$args['post__in'] = $ids;
	}
	$items = array();
	foreach ( get_posts( $args ) as $post ) {
		$seo     = wpmcp_seo( $post->ID );
		$items[] = array(
			'id'           => (int) $post->ID,
			'type'         => $post->post_type,
			'lang'         => wpmcp_post_lang( $post->ID ),
			'link'         => get_permalink( $post ),
			'noindex'      => ! empty( $seo['noindex'] ),
			'password'     => '' !== $post->post_password,
			'translations' => wpmcp_post_translations( $post->ID ),
		);
	}
	return array( 'items' => $items );
}

function wpmcp_rest_content_get( WP_REST_Request $request ): array {
	return wpmcp_content_detail( get_post( (int) $request['id'] ) );
}

/**
 * Everything about one post or page: what get_content returns, and what writes answer.
 *
 * Small fields first and the body last: a client that cuts a long answer short (ChatGPT
 * drops whole lines past its budget) still gets the version, the status and the length.
 */
function wpmcp_content_detail( WP_Post $post ): array {
	$thumb_id = (int) get_post_thumbnail_id( $post );
	$seo      = wpmcp_seo( $post->ID );
	if ( ! function_exists( 'wp_check_post_lock' ) ) {
		require_once ABSPATH . 'wp-admin/includes/post.php';
	}
	// Someone else has the editor open (the ID of that user is not returned).
	$lock    = wp_check_post_lock( $post->ID );
	$content = wpmcp_decode_emoji( $post->post_content );

	return array_merge(
		array(
			'id'             => (int) $post->ID,
			'version'        => wpmcp_content_version( $post, $seo ),
			'being_edited'   => (bool) $lock,
			'content_length' => mb_strlen( $content ),
		),
		wpmcp_post_summary( $post ),
		array(
			'slug'               => $post->post_name,
			'excerpt'            => wpmcp_decode_emoji( $post->post_excerpt ),
			'content_format'     => has_blocks( $post->post_content ) ? 'blocks' : 'classic',
			'categories'         => 'post' === $post->post_type ? wpmcp_terms_of( $post->ID, 'category' ) : array(),
			'tags'               => 'post' === $post->post_type ? wpmcp_terms_of( $post->ID, 'post_tag' ) : array(),
			'featured_media'     => $thumb_id ? wpmcp_media_summary( get_post( $thumb_id ) ) : null,
			'parent'             => $post->post_parent ? (int) $post->post_parent : null,
			'menu_order'         => (int) $post->menu_order,
			'template'           => (string) get_page_template_slug( $post ),
			'sticky'             => 'post' === $post->post_type && is_sticky( $post->ID ),
			'password_protected' => '' !== $post->post_password,
			'edit_url'           => admin_url( 'post.php?post=' . $post->ID . '&action=edit' ),
			'preview_url'        => 'publish' === $post->post_status ? null : get_preview_post_link( $post ),
			'seo'                => $seo,
			'extra'              => wpmcp_extra_fields( $post ),
			'content'            => $content,
		)
	);
}

function wpmcp_rest_revisions( WP_REST_Request $request ): array {
	$items = array();
	foreach ( wp_get_post_revisions( (int) $request['id'], array( 'posts_per_page' => WPMCP_BRIDGE_PER_PAGE ) ) as $revision ) {
		$items[] = array(
			'id'             => (int) $revision->ID,
			'date_gmt'       => wpmcp_iso( $revision->post_modified_gmt ),
			'title'          => wpmcp_title( $revision ),
			'content_length' => mb_strlen( $revision->post_content ),
			'autosave'       => wp_is_post_autosave( $revision ) ? true : false,
		);
	}
	return array( 'post_id' => (int) $request['id'], 'items' => $items );
}

function wpmcp_rest_terms( WP_REST_Request $request ) {
	$lang = wpmcp_lang_arg( (string) $request['lang'] );
	if ( is_wp_error( $lang ) ) {
		return $lang;
	}
	$args = array( 'taxonomy' => (string) $request['taxonomy'], 'hide_empty' => false, 'lang' => $lang, 'number' => 500 );
	if ( '' !== $request['search'] ) {
		$args['search'] = (string) $request['search'];
	}
	return array(
		'taxonomy' => (string) $request['taxonomy'],
		'items'    => array_map( 'wpmcp_term_summary', get_terms( $args ) ),
	);
}

function wpmcp_term_summary( WP_Term $term ): array {
	return array(
		'id'           => (int) $term->term_id,
		'name'         => html_entity_decode( $term->name, ENT_QUOTES | ENT_HTML5, 'UTF-8' ),
		'slug'         => $term->slug,
		'parent'       => $term->parent ? (int) $term->parent : null,
		'count'        => (int) $term->count,
		'description'  => wpmcp_plain( $term->description, 300 ),
		'lang'         => wpmcp_term_lang( (int) $term->term_id ),
		'translations' => wpmcp_term_translations( (int) $term->term_id ),
	);
}

function wpmcp_rest_media( WP_REST_Request $request ): array {
	$args = array(
		'post_type'      => 'attachment',
		'post_status'    => 'inherit',
		'post_mime_type' => 'image',
		'paged'          => (int) $request['page'],
		'posts_per_page' => (int) $request['per_page'],
		'orderby'        => 'date',
		'order'          => 'DESC',
		'lang'           => '',
	);
	if ( '' !== $request['search'] ) {
		$args['s'] = (string) $request['search'];
	}
	if ( $request['parent'] ) {
		$args['post_parent'] = (int) $request['parent'];
	}
	$query = new WP_Query( $args );
	return wpmcp_paged( $query, array_map( 'wpmcp_media_summary', $query->posts ), (int) $request['page'], (int) $request['per_page'] );
}

function wpmcp_rest_changes( WP_REST_Request $request ) {
	$since = '' === $request['since'] ? gmdate( 'Y-m-d H:i:s', time() - 7 * DAY_IN_SECONDS ) : wpmcp_gmt( (string) $request['since'] );
	if ( null === $since ) {
		return wpmcp_error( 'wpmcp_bad_since', 'since must be a date, for example 2026-11-01T00:00:00Z.' );
	}
	$query = new WP_Query(
		array(
			'post_type'           => WPMCP_BRIDGE_TYPES,
			'post_status'         => WPMCP_BRIDGE_STATUSES,
			'lang'                => '',
			'posts_per_page'      => (int) $request['limit'],
			'orderby'             => 'modified',
			'order'               => 'DESC',
			'ignore_sticky_posts' => true,
			// Local time: new drafts have no post_modified_gmt yet (see wpmcp_modified_gmt).
			'date_query'          => array( array( 'column' => 'post_modified', 'after' => get_date_from_gmt( $since ), 'inclusive' => true ) ),
		)
	);
	$items = array();
	foreach ( $query->posts as $post ) {
		$summary = wpmcp_post_summary( $post );
		unset( $summary['excerpt'] );
		$items[] = $summary;
	}
	return array( 'since_gmt' => wpmcp_iso( $since ), 'total' => (int) $query->found_posts, 'items' => $items );
}
