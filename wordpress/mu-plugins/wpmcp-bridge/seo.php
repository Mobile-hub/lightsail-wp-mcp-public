<?php
/**
 * SEO fields of a post or page, from whichever SEO plugin the site runs: All in One SEO
 * (its own table, through its model) or SEOPress (post meta). Every tool sees the same
 * fields (`seo`: title, description, focus_keyphrase, og_title, og_description, plus the
 * read-only canonical_url and robots flags) and `seo.plugin` says which plugin keeps them.
 *
 * Writes need exactly one of the two active: with none, or both, a change that sets SEO
 * fields is refused as a whole rather than saved without them. Only the fields given
 * change; null (or an empty string) clears one. Texts are stored as each plugin's own
 * editor stores them.
 */

defined( 'ABSPATH' ) || exit;

const WPMCP_BRIDGE_AIOSEO_MODEL = '\\AIOSEO\\Plugin\\Common\\Models\\Post';
// SEOPress: the writable fields and their post meta (the keyphrases are one
// comma-separated list, the first being the focus keyphrase).
const WPMCP_BRIDGE_SEOPRESS_META = array(
	'title'          => '_seopress_titles_title',
	'description'    => '_seopress_titles_desc',
	'og_title'       => '_seopress_social_fb_title',
	'og_description' => '_seopress_social_fb_desc',
);
const WPMCP_BRIDGE_SEOPRESS_KEYWORDS = '_seopress_analysis_target_kw';

/** The active SEO plugins, in order: 'aioseo', 'seopress'. */
function wpmcp_seo_plugins(): array {
	$plugins = array();
	if ( class_exists( WPMCP_BRIDGE_AIOSEO_MODEL ) ) {
		$plugins[] = 'aioseo';
	}
	if ( defined( 'SEOPRESS_VERSION' ) ) {
		$plugins[] = 'seopress';
	}
	return $plugins;
}

/** The plugin SEO fields are written to, or an error when there is not exactly one. */
function wpmcp_seo_writable() {
	$plugins = wpmcp_seo_plugins();
	if ( ! $plugins ) {
		return wpmcp_error( 'wpmcp_no_seo', 'No SEO plugin is active (All in One SEO or SEOPress): nothing was changed. Leave out the SEO fields.', 409 );
	}
	if ( count( $plugins ) > 1 ) {
		return wpmcp_error( 'wpmcp_seo_conflict', 'Both All in One SEO and SEOPress are active, so it is unclear which one the site uses: nothing was changed. The site owner must deactivate one.', 409 );
	}
	return $plugins[0];
}

/** SEO fields of a post, or null when no SEO plugin is active. */
function wpmcp_seo( int $post_id ): ?array {
	$plugin = wpmcp_seo_plugins()[0] ?? null;
	if ( 'aioseo' === $plugin ) {
		return array( 'plugin' => 'aioseo' ) + wpmcp_seo_aioseo( $post_id );
	}
	if ( 'seopress' === $plugin ) {
		return array( 'plugin' => 'seopress' ) + wpmcp_seo_seopress( $post_id );
	}
	return null;
}

/** Writes only the given SEO fields. */
function wpmcp_save_seo( int $post_id, array $seo ): ?WP_Error {
	if ( ! $seo ) {
		return null;
	}
	$plugin = wpmcp_seo_writable();
	if ( is_wp_error( $plugin ) ) {
		return $plugin;
	}
	return 'aioseo' === $plugin ? wpmcp_save_seo_aioseo( $post_id, $seo ) : wpmcp_save_seo_seopress( $post_id, $seo );
}

// --------------------------------------------------------------------- All in One SEO

function wpmcp_seo_aioseo( int $post_id ): array {
	$model = WPMCP_BRIDGE_AIOSEO_MODEL;
	$row   = $model::getPost( $post_id );
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

/** AIOSEO 5 saves patches, not whole rows. */
function wpmcp_save_seo_aioseo( int $post_id, array $seo ): ?WP_Error {
	$model = WPMCP_BRIDGE_AIOSEO_MODEL;
	$data  = array();
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

// --------------------------------------------------------------------------- SEOPress

/** A SEOPress text field, entities decoded (some of its importers store them escaped). */
function wpmcp_seopress_meta( int $post_id, string $key ): ?string {
	$value = trim( html_entity_decode( (string) get_post_meta( $post_id, $key, true ), ENT_QUOTES | ENT_HTML5, 'UTF-8' ) );
	return '' === $value ? null : $value;
}

/** SEOPress's keyphrases, in order. */
function wpmcp_seopress_keywords( int $post_id ): array {
	$list = explode( ',', (string) wpmcp_seopress_meta( $post_id, WPMCP_BRIDGE_SEOPRESS_KEYWORDS ) );
	return array_values( array_filter( array_map( 'trim', $list ), 'strlen' ) );
}

function wpmcp_seo_seopress( int $post_id ): array {
	$noindex  = 'yes' === get_post_meta( $post_id, '_seopress_robots_index', true );
	$nofollow = 'yes' === get_post_meta( $post_id, '_seopress_robots_follow', true );
	$fields   = array();
	foreach ( WPMCP_BRIDGE_SEOPRESS_META as $field => $key ) {
		$fields[ $field ] = wpmcp_seopress_meta( $post_id, $key );
	}
	return array(
		'title'           => $fields['title'],
		'description'     => $fields['description'],
		'focus_keyphrase' => wpmcp_seopress_keywords( $post_id )[0] ?? null,
		'canonical_url'   => wpmcp_seopress_meta( $post_id, '_seopress_robots_canonical' ),
		// SEOPress's per-post flags only ever add noindex or nofollow to the site's defaults.
		'robots_default'  => ! $noindex && ! $nofollow,
		'noindex'         => $noindex,
		'nofollow'        => $nofollow,
		'og_title'        => $fields['og_title'],
		'og_description'  => $fields['og_description'],
	);
}

/** As SEOPress's own editor saves them; an empty value removes the field. */
function wpmcp_save_seo_seopress( int $post_id, array $seo ): ?WP_Error {
	$put = static function ( string $key, string $value ) use ( $post_id ): void {
		'' === $value ? delete_post_meta( $post_id, $key ) : update_post_meta( $post_id, $key, wp_slash( $value ) );
	};
	foreach ( WPMCP_BRIDGE_SEOPRESS_META as $field => $key ) {
		if ( array_key_exists( $field, $seo ) ) {
			$value = (string) $seo[ $field ];
			$put( $key, in_array( $field, array( 'description', 'og_description' ), true ) ? sanitize_textarea_field( $value ) : sanitize_text_field( $value ) );
		}
	}
	if ( array_key_exists( 'focus_keyphrase', $seo ) ) {
		// Replace only the first keyphrase (the focus one); keep the others.
		$list  = wpmcp_seopress_keywords( $post_id );
		$focus = trim( str_replace( ',', ' ', sanitize_text_field( (string) $seo['focus_keyphrase'] ) ) );
		array_shift( $list );
		$put( WPMCP_BRIDGE_SEOPRESS_KEYWORDS, implode( ',', '' === $focus ? $list : array_merge( array( $focus ), $list ) ) );
	}
	return null;
}
