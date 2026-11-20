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
 *
 * With SEOPress, its content analysis of a published item (wpmcp/analyze-seopress-content)
 * comes through here too, by its own ability.
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

// ------------------------------------------------------------ SEOPress content analysis

const WPMCP_BRIDGE_SEOPRESS_ANALYSIS = 'seopress/analyze-post-content';
// Most serious first. "low" is a suggestion: SEOPress counts it as passed.
const WPMCP_BRIDGE_SEOPRESS_IMPACTS = array( 'high', 'medium', 'low', 'good' );
const WPMCP_BRIDGE_FINDING_CHARS    = 400;

/** The SEO operations: routes of wpmcp/v1 and abilities (see abilities.php). */
function wpmcp_seo_operations(): array {
	return array(
		array(
			'ability'             => 'wpmcp/analyze-seopress-content',
			'label'               => 'SEOPress content analysis',
			'description'         => 'SEOPress\'s content analysis of a published post or page, run on the page as visitors get it: how many checks pass and fail, and each check with its impact and finding as plain text.',
			'route'               => '/content/(?P<id>\d+)/seo-analysis',
			'methods'             => WP_REST_Server::READABLE,
			'callback'            => 'wpmcp_rest_seo_analysis',
			'permission_callback' => 'wpmcp_can_edit_item',
			'args'                => array(
				'id' => array( 'type' => 'integer', 'minimum' => 1, 'required' => true ),
			),
			// Nothing visitors see changes. SEOPress keeps the result as the item's score,
			// as it does when its editor panel runs the analysis.
			'annotations'         => wpmcp_annotations( true, false, true ),
		),
	);
}

/**
 * Runs SEOPress's own ability, which loads the item's page as a visitor (a request from
 * the server to the site's address, without the caller's credentials) and scores it. A
 * draft has no page a visitor can load, so only published items are analysed.
 */
function wpmcp_rest_seo_analysis( WP_REST_Request $request ) {
	$post = get_post( (int) $request['id'] );
	if ( ! in_array( 'seopress', wpmcp_seo_plugins(), true ) ) {
		return wpmcp_error( 'wpmcp_no_seopress', 'The content analysis is SEOPress\'s, and SEOPress is not active on this site.', 409 );
	}
	if ( ! function_exists( 'wp_has_ability' ) || ! wp_has_ability( WPMCP_BRIDGE_SEOPRESS_ANALYSIS ) ) {
		return wpmcp_error( 'wpmcp_no_seopress_analysis', 'This SEOPress does not offer its content analysis to other plugins: it needs SEOPress 9.9 or later and WordPress 6.9 or later.', 409 );
	}
	if ( function_exists( 'seopress_metabox_role_is_blocked' ) && seopress_metabox_role_is_blocked( 'CONTENT_ANALYSIS' ) ) {
		return wpmcp_error( 'wpmcp_seopress_analysis_hidden', 'The site\'s SEOPress settings hide the content analysis from editors.', 403 );
	}
	if ( 'publish' !== $post->post_status || '' !== $post->post_password ) {
		$state = '' !== $post->post_password ? 'password-protected' : 'a ' . $post->post_status . ' item';
		return wpmcp_error(
			'wpmcp_not_public',
			sprintf( 'Item %d is %s: SEOPress analyses the page as visitors get it, so only published posts and pages without a password can be analysed. Check the SEO fields of a draft with get_content.', $post->ID, $state ),
			409
		);
	}

	$data = wp_get_ability( WPMCP_BRIDGE_SEOPRESS_ANALYSIS )->execute( array( 'post_id' => $post->ID ) );
	if ( is_wp_error( $data ) ) {
		$status = (int) ( $data->get_error_data()['status'] ?? 0 );
		if ( 429 === $status ) {
			return wpmcp_error( 'wpmcp_seopress_busy', 'SEOPress allows only so many analyses a minute: try again in a minute.', 429 );
		}
		return wpmcp_error( 'wpmcp_seopress_failed', 'SEOPress could not analyse the page: ' . wp_strip_all_tags( $data->get_error_message() ), 409 );
	}
	if ( ! is_array( $data ) || ! isset( $data['score'] ) || ! is_array( $data['score'] ) ) {
		// When the page cannot be loaded, SEOPress answers why in place of the title.
		$why = is_array( $data ) && is_string( $data['title'] ?? null ) ? wp_strip_all_tags( $data['title'] ) : '';
		return wpmcp_error( 'wpmcp_seopress_failed', trim( 'SEOPress could not load the page as visitors get it. ' . $why ), 409 );
	}
	return wpmcp_seopress_analysis_summary( $post, $data['score'] );
}

/** The checks that ran, most serious first, as SEOPress counts them (ignored ones apart). */
function wpmcp_seopress_analysis_summary( WP_Post $post, array $score ): array {
	$ignored = wpmcp_seopress_ignored_checks( $post->ID );
	$details = wpmcp_seopress_check_details( $post );
	$checks  = array();
	foreach ( $score as $key => $impact ) {
		// null: the check did not run on this page.
		if ( ! is_string( $key ) || ! in_array( $impact, WPMCP_BRIDGE_SEOPRESS_IMPACTS, true ) || in_array( $key, $ignored, true ) ) {
			continue;
		}
		$detail   = is_array( $details[ $key ] ?? null ) ? $details[ $key ] : array();
		$checks[] = array(
			'check'   => $key,
			'title'   => is_string( $detail['title'] ?? null ) ? $detail['title'] : $key,
			'impact'  => $impact,
			'passed'  => in_array( $impact, array( 'low', 'good' ), true ),
			'finding' => wpmcp_seopress_text( $detail['desc'] ?? '' ),
		);
	}
	$rank = array_flip( WPMCP_BRIDGE_SEOPRESS_IMPACTS );
	usort( $checks, static fn ( $a, $b ) => $rank[ $a['impact'] ] <=> $rank[ $b['impact'] ] );

	$passed   = count( array_filter( array_column( $checks, 'passed' ) ) );
	$failed   = count( $checks ) - $passed;
	$keywords = wpmcp_seopress_keywords( $post->ID );
	$result   = array(
		'id'            => $post->ID,
		'link'          => get_permalink( $post ),
		'plugin'        => 'seopress',
		'keywords'      => $keywords,
		'verdict'       => 0 === $failed ? 'good' : 'needs_improvement',
		'score_summary' => array(
			'passed' => $passed,
			'failed' => $failed,
			'total'  => count( $checks ),
		),
		'checks'        => $checks,
	);
	if ( $ignored ) {
		$result['ignored'] = $ignored;
	}
	if ( ! $keywords ) {
		$result['notes'] = array( 'No focus keyphrase is set, so the keyword checks have nothing to look for: set seo.focus_keyphrase first.' );
	}
	return $result;
}

/** Title and finding (HTML) of each check, from the analysis SEOPress has just stored. */
function wpmcp_seopress_check_details( WP_Post $post ): array {
	if ( ! function_exists( 'seopress_get_service' ) ) {
		return array();
	}
	try {
		$analyzes = seopress_get_service( 'GetContentAnalysis' )->getAnalyzes( $post );
	} catch ( Throwable $e ) {
		return array();
	}
	return is_array( $analyzes ) ? $analyzes : array();
}

/** Checks the site owner chose to ignore for this item (SEOPress PRO). */
function wpmcp_seopress_ignored_checks( int $post_id ): array {
	$helper = '\\SEOPress\\Helpers\\ContentAnalysis';
	if ( ! method_exists( $helper, 'getIgnoredChecks' ) ) {
		return array();
	}
	try {
		return array_values( array_filter( (array) $helper::getIgnoredChecks( $post_id ), 'is_string' ) );
	} catch ( Throwable $e ) {
		return array();
	}
}

/** A finding as plain text: headings, paragraphs and list items apart, no upsell links. */
function wpmcp_seopress_text( $html ): string {
	$html = (string) preg_replace( '#<a\b[^>]*\bseopress-help\b[^>]*>.*?</a>#is', '', (string) $html );
	$html = (string) preg_replace( '#</h[1-6]>#i', ': ', $html );
	$html = (string) preg_replace( '#<(?:/p|/li|/ul|br\s*/?)>#i', ' ', $html );
	$text = html_entity_decode( wp_strip_all_tags( $html ), ENT_QUOTES | ENT_HTML5, 'UTF-8' );
	$text = trim( (string) preg_replace( '/\s+/u', ' ', $text ) );
	if ( mb_strlen( $text ) <= WPMCP_BRIDGE_FINDING_CHARS ) {
		return $text;
	}
	return rtrim( mb_substr( $text, 0, WPMCP_BRIDGE_FINDING_CHARS - 1 ) ) . '…';
}
