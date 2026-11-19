<?php
/**
 * Shared helpers: shapes of the objects the bridge returns. Lists carry plain-text excerpts
 * only; full bodies come from the single-item route. Dates are UTC, ISO 8601.
 */

defined( 'ABSPATH' ) || exit;

const WPMCP_BRIDGE_TYPES    = array( 'post', 'page' );
const WPMCP_BRIDGE_STATUSES = array( 'publish', 'future', 'draft', 'pending', 'private', 'trash' );

/** Database GMT datetime ('Y-m-d H:i:s') to ISO 8601 UTC, or null when unset. */
function wpmcp_iso( $gmt ): ?string {
	if ( ! $gmt || '0000-00-00 00:00:00' === $gmt ) {
		return null;
	}
	return str_replace( ' ', 'T', (string) $gmt ) . 'Z';
}

/**
 * When the item last changed, GMT. A new draft keeps post_modified_gmt at zero until its
 * first update (WordPress copies the empty draft post_date_gmt), so derive it then.
 */
function wpmcp_modified_gmt( WP_Post $post ): string {
	if ( $post->post_modified_gmt && '0000-00-00 00:00:00' !== $post->post_modified_gmt ) {
		return $post->post_modified_gmt;
	}
	return get_gmt_from_date( $post->post_modified );
}

/**
 * Fingerprint of everything update_draft can change. Edits must name the version they were
 * based on: post_modified has one-second resolution and SEO changes do not touch it, so it
 * cannot tell two quick edits apart on its own.
 */
function wpmcp_content_version( WP_Post $post, ?array $seo ): string {
	$categories = wp_get_post_categories( $post->ID );
	sort( $categories );
	$state = array(
		wpmcp_modified_gmt( $post ),
		$post->post_status,
		$post->post_title,
		$post->post_content,
		$post->post_excerpt,
		$post->post_name,
		(int) $post->post_parent,
		(int) get_post_thumbnail_id( $post ),
		$categories,
		$seo,
	);
	return substr( hash( 'sha256', wp_json_encode( $state ) ), 0, 16 );
}

/** ISO 8601 (or anything strtotime reads) to a database GMT datetime, or null. */
function wpmcp_gmt( $value ): ?string {
	if ( ! is_string( $value ) || '' === $value ) {
		return null;
	}
	$time = strtotime( $value );
	return false === $time ? null : gmdate( 'Y-m-d H:i:s', $time );
}

/** Plain text, no markup, no shortcodes, entities decoded, cut at $length characters. */
function wpmcp_plain( $html, int $length = 300 ): string {
	$text = excerpt_remove_blocks( (string) $html );
	$text = strip_shortcodes( $text );
	$text = wp_strip_all_tags( $text, true );
	$text = html_entity_decode( $text, ENT_QUOTES | ENT_HTML5, 'UTF-8' );
	$text = trim( preg_replace( '/\s+/u', ' ', $text ) );
	if ( mb_strlen( $text ) > $length ) {
		$text = rtrim( mb_substr( $text, 0, $length - 1 ) ) . '…';
	}
	return $text;
}

/**
 * Emoji back from the entities WordPress stores them as in utf8mb3 tables (wp_encode_emoji).
 * Only characters beyond the BMP are touched, so other entities stay as the author wrote
 * them; saving encodes them again, so a read-edit-write round trip changes nothing.
 */
function wpmcp_decode_emoji( string $html ): string {
	return preg_replace_callback(
		'/&#(x[0-9a-f]{5,6}|[0-9]{5,7});/i',
		static function ( $match ) {
			$code = 'x' === strtolower( $match[1][0] ) ? hexdec( substr( $match[1], 1 ) ) : (int) $match[1];
			return ( $code >= 0x10000 && $code <= 0x10FFFF ) ? mb_chr( $code, 'UTF-8' ) : $match[0];
		},
		$html
	);
}

function wpmcp_title( WP_Post $post ): string {
	return html_entity_decode( wp_strip_all_tags( $post->post_title ), ENT_QUOTES | ENT_HTML5, 'UTF-8' );
}

function wpmcp_has_polylang(): bool {
	return function_exists( 'pll_get_post_language' );
}

function wpmcp_languages(): array {
	return wpmcp_has_polylang() ? pll_languages_list() : array();
}

function wpmcp_post_lang( int $id ): ?string {
	return wpmcp_has_polylang() ? ( pll_get_post_language( $id ) ?: null ) : null;
}

/** Translations of a post as language => post ID, the post itself included. */
function wpmcp_post_translations( int $id ): array {
	if ( ! wpmcp_has_polylang() ) {
		return array();
	}
	$translations = array_map( 'intval', array_filter( pll_get_post_translations( $id ) ) );
	ksort( $translations );
	return $translations;
}

function wpmcp_term_lang( int $id ): ?string {
	return function_exists( 'pll_get_term_language' ) ? ( pll_get_term_language( $id ) ?: null ) : null;
}

function wpmcp_term_translations( int $id ): array {
	if ( ! function_exists( 'pll_get_term_translations' ) ) {
		return array();
	}
	$translations = array_map( 'intval', array_filter( pll_get_term_translations( $id ) ) );
	ksort( $translations );
	return $translations;
}

function wpmcp_terms_of( int $post_id, string $taxonomy ): array {
	$terms = get_the_terms( $post_id, $taxonomy );
	if ( ! is_array( $terms ) ) {
		return array();
	}
	return array_map(
		static fn ( $term ) => array(
			'id'   => (int) $term->term_id,
			'name' => html_entity_decode( $term->name, ENT_QUOTES | ENT_HTML5, 'UTF-8' ),
		),
		$terms
	);
}

/** The short form used in lists and search results. */
function wpmcp_post_summary( WP_Post $post ): array {
	$excerpt = '' !== trim( $post->post_excerpt ) ? $post->post_excerpt : $post->post_content;
	return array(
		'id'           => (int) $post->ID,
		'type'         => $post->post_type,
		'status'       => $post->post_status,
		'lang'         => wpmcp_post_lang( $post->ID ),
		'title'        => wpmcp_title( $post ),
		'excerpt'      => post_password_required( $post ) ? '' : wpmcp_plain( $excerpt ),
		'link'         => get_permalink( $post ),
		'date_gmt'     => wpmcp_iso( $post->post_date_gmt ),
		'modified_gmt' => wpmcp_iso( wpmcp_modified_gmt( $post ) ),
		'categories'   => 'post' === $post->post_type ? array_column( wpmcp_terms_of( $post->ID, 'category' ), 'id' ) : array(),
		'translations' => wpmcp_post_translations( $post->ID ),
	);
}

/** SEO fields kept by All in One SEO for a post, or null when the plugin is not active. */
function wpmcp_seo( int $post_id ): ?array {
	if ( ! class_exists( '\\AIOSEO\\Plugin\\Common\\Models\\Post' ) ) {
		return null;
	}
	$row   = \AIOSEO\Plugin\Common\Models\Post::getPost( $post_id );
	$found = $row && $row->exists();
	$field = static fn ( string $name ) => $found && ! empty( $row->$name ) ? $row->$name : null;

	// AIOSEO 5 keeps the focus keyphrase in its own column; older rows only in `keyphrases`.
	$focus = $field( 'focus_keyword' );
	if ( null === $focus ) {
		$keyphrases = $field( 'keyphrases' );
		if ( is_string( $keyphrases ) ) {
			$keyphrases = json_decode( $keyphrases );
		}
		$focus = ( is_object( $keyphrases ) && ! empty( $keyphrases->focus->keyphrase ) ) ? $keyphrases->focus->keyphrase : null;
	}
	// With robots_default set, the site-wide defaults apply and the per-post flags are ignored.
	$own_robots = $found && empty( $row->robots_default );
	return array(
		'title'           => $field( 'title' ),
		'description'     => $field( 'description' ),
		'focus_keyphrase' => is_string( $focus ) ? $focus : null,
		'canonical_url'   => $field( 'canonical_url' ),
		'robots_default'  => ! $own_robots,
		'noindex'         => $own_robots && ! empty( $row->robots_noindex ),
		'nofollow'        => $own_robots && ! empty( $row->robots_nofollow ),
		'og_title'        => $field( 'og_title' ),
		'og_description'  => $field( 'og_description' ),
	);
}

/**
 * Site-specific, read-only fields for get_content (for example the meta of a custom
 * plugin), from another mu-plugin:
 *
 *   add_filter( 'wpmcp_bridge_extra_fields', function ( array $extra, WP_Post $post ) {
 *       $extra['book'] = array( 'isbn' => get_post_meta( $post->ID, '_isbn', true ) );
 *       return $extra;
 *   }, 10, 2 );
 *
 * The result goes to the model as is: return plain, JSON-serialisable values, and nothing
 * the integration user should not read. Not part of the edit version, and never writable.
 */
function wpmcp_extra_fields( WP_Post $post ): ?array {
	$extra = apply_filters( 'wpmcp_bridge_extra_fields', array(), $post );
	return is_array( $extra ) && $extra ? $extra : null;
}

function wpmcp_media_summary( WP_Post $attachment ): array {
	$meta = wp_get_attachment_metadata( $attachment->ID );
	return array(
		'id'          => (int) $attachment->ID,
		'title'       => wpmcp_title( $attachment ),
		'alt'         => (string) get_post_meta( $attachment->ID, '_wp_attachment_image_alt', true ),
		'caption'     => wpmcp_plain( $attachment->post_excerpt, 500 ),
		'mime_type'   => $attachment->post_mime_type,
		'url'         => wp_get_attachment_url( $attachment->ID ),
		'width'       => isset( $meta['width'] ) ? (int) $meta['width'] : null,
		'height'      => isset( $meta['height'] ) ? (int) $meta['height'] : null,
		'date_gmt'    => wpmcp_iso( $attachment->post_date_gmt ),
		'attached_to' => $attachment->post_parent ? (int) $attachment->post_parent : null,
	);
}

/**
 * A URL reduced for comparison: host and path in lower case, without scheme, query,
 * fragment or trailing slash ('' when it has no host). Site-relative paths are read
 * against the site's address.
 */
function wpmcp_link_key( string $url ): string {
	$url = trim( html_entity_decode( $url, ENT_QUOTES | ENT_HTML5, 'UTF-8' ) );
	if ( str_starts_with( $url, '/' ) && ! str_starts_with( $url, '//' ) ) {
		$url = home_url( $url );
	}
	$parts = wp_parse_url( $url );
	if ( ! is_array( $parts ) || empty( $parts['host'] ) ) {
		return '';
	}
	return strtolower( $parts['host'] . untrailingslashit( rawurldecode( $parts['path'] ?? '' ) ) );
}

/** Whether $html has a link (<a href>) to the address of $target. */
function wpmcp_links_to( string $html, WP_Post $target ): bool {
	$wanted = wpmcp_link_key( (string) get_permalink( $target ) );
	if ( '' === $wanted || ! preg_match_all( '/<a\s[^>]*?\bhref\s*=\s*(["\'])(.*?)\1/is', $html, $found ) ) {
		return false;
	}
	foreach ( $found[2] as $href ) {
		if ( wpmcp_link_key( $href ) === $wanted ) {
			return true;
		}
	}
	return false;
}

/**
 * Elements that saving $html would remove for the current user (anyone without
 * unfiltered_html, the integration user included: kses filters what it saves), as tag
 * names. Body text is kept by kses; scripts, iframes, forms and the like are not.
 */
function wpmcp_kses_losses( string $html ): array {
	if ( current_user_can( 'unfiltered_html' ) ) {
		return array();
	}
	$count  = static function ( string $markup ): array {
		preg_match_all( '#<([a-z][a-z0-9-]*)[\s/>]#i', $markup, $tags );
		return array_count_values( array_map( 'strtolower', $tags[1] ) );
	};
	$after  = $count( wp_kses_post( $html ) );
	$losses = array();
	foreach ( $count( $html ) as $tag => $number ) {
		if ( ( $after[ $tag ] ?? 0 ) < $number ) {
			$losses[] = "<$tag>";
		}
	}
	return $losses;
}

function wpmcp_error( string $code, string $message, int $status = 400 ): WP_Error {
	return new WP_Error( $code, $message, array( 'status' => $status ) );
}
