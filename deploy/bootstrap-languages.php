<?php
/**
 * Polylang languages for a fresh site, from SITE_LANGUAGES (WordPress locales, comma
 * separated; the first is the default). Existing languages are kept; items without a
 * language get the default one. Run by bootstrap-site.sh through `wp eval-file`.
 */

if ( ! function_exists( 'PLL' ) || ! function_exists( 'pll_languages_list' ) ) {
	WP_CLI::error( 'Polylang is not active.' );
}

$locales = array_filter( explode( ',', (string) getenv( 'SITE_LANGUAGES' ) ) );
if ( ! $locales ) {
	WP_CLI::error( 'SITE_LANGUAGES is empty.' );
}

// Polylang's own list of known languages: name, slug and flag for each locale.
// (src/settings/ in current Polylang, settings/ in older versions).
$predefined = array();
foreach ( array( '/src/settings/languages.php', '/settings/languages.php' ) as $list ) {
	if ( is_readable( POLYLANG_DIR . $list ) ) {
		$predefined = include POLYLANG_DIR . $list;
		break;
	}
}

$model    = PLL()->model;
$existing = pll_languages_list( array( 'fields' => 'locale' ) );
$order    = count( $existing );
foreach ( $locales as $locale ) {
	if ( in_array( $locale, $existing, true ) ) {
		continue;
	}
	$known  = is_array( $predefined ) && isset( $predefined[ $locale ] ) ? $predefined[ $locale ] : array();
	$result = $model->add_language(
		array(
			'name'       => $known['name'] ?? $locale,
			'slug'       => $known['code'] ?? strtolower( strtok( $locale, '_' ) ),
			'locale'     => $locale,
			'rtl'        => isset( $known['dir'] ) && 'rtl' === $known['dir'],
			'term_group' => $order++,
			'flag'       => $known['flag'] ?? '',
		)
	);
	if ( is_wp_error( $result ) ) {
		WP_CLI::error( "$locale: " . $result->get_error_message() );
	}
	WP_CLI::log( "added $locale" );
}
$model->clean_languages_cache();

$default = $model->get_language( $locales[0] );
if ( ! $default ) {
	WP_CLI::error( "default language {$locales[0]} not found" );
}
$options = get_option( 'polylang', array() );
if ( empty( $options['default_lang'] ) ) {
	$options['default_lang'] = $default->slug;
	$options['hide_default'] = true;
	update_option( 'polylang', $options );
}
if ( method_exists( $model, 'set_language_in_mass' ) ) {
	$model->set_language_in_mass( $default );
}
$model->clean_languages_cache();
WP_CLI::success( 'languages: ' . implode( ',', pll_languages_list() ) . '; default: ' . pll_default_language() );
