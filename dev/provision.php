<?php
/**
 * Fixture content for the local test stack: three languages, linked categories,
 * and one review in each language, linked as translations. Idempotent.
 *
 * Run with: bin/dev wp eval-file /dev-scripts/provision.php
 */

if ( ! function_exists( 'pll_languages_list' ) ) {
	WP_CLI::error( 'Polylang is not active.' );
}

// Three languages for the fixtures: English is the default, without a URL prefix.
$languages = array(
	'en' => array( 'name' => 'English', 'locale' => 'en_GB', 'flag' => 'gb' ),
	'es' => array( 'name' => 'Español', 'locale' => 'es_ES', 'flag' => 'es' ),
	'pt' => array( 'name' => 'Português', 'locale' => 'pt_PT', 'flag' => 'pt' ),
);
$order     = 0;
foreach ( $languages as $slug => $language ) {
	if ( in_array( $slug, pll_languages_list(), true ) ) {
		continue;
	}
	$result = PLL()->model->add_language(
		array(
			'name'       => $language['name'],
			'slug'       => $slug,
			'locale'     => $language['locale'],
			'rtl'        => false,
			'term_group' => $order++,
			'flag'       => $language['flag'],
		)
	);
	if ( is_wp_error( $result ) ) {
		WP_CLI::error( "language $slug: " . $result->get_error_message() );
	}
}
PLL()->model->clean_languages_cache();
$options                 = get_option( 'polylang' );
$options['default_lang'] = 'en';
$options['hide_default'] = true;
$options['force_lang']   = 1;
update_option( 'polylang', $options );

// One category per language, linked.
$category_names = array( 'en' => 'Adventure', 'es' => 'Aventura', 'pt' => 'Aventura PT' );
$category_ids   = array();
foreach ( $category_names as $lang => $name ) {
	$existing = get_term_by( 'name', $name, 'category' );
	$term_id  = $existing ? $existing->term_id : wp_insert_term( $name, 'category' )['term_id'];
	pll_set_term_language( $term_id, $lang );
	$category_ids[ $lang ] = $term_id;
}
pll_save_term_translations( $category_ids );

// One published review per language, linked.
$titles   = array(
	'en' => 'The Little Prince',
	'es' => 'El Principito',
	'pt' => 'O Principezinho',
);
$post_ids = array();
foreach ( $titles as $lang => $title ) {
	$found = get_posts(
		array( 'title' => $title, 'post_type' => 'post', 'post_status' => 'any', 'lang' => '', 'numberposts' => 1 )
	);
	if ( $found ) {
		$post_ids[ $lang ] = $found[0]->ID;
		continue;
	}
	$post_ids[ $lang ] = wp_insert_post(
		array(
			'post_title'    => $title,
			'post_status'   => 'publish',
			'post_type'     => 'post',
			'post_content'  => "<!-- wp:paragraph -->\n<p>A fixture review of $title ($lang).</p>\n<!-- /wp:paragraph -->",
			'post_category' => array( $category_ids[ $lang ] ),
		)
	);
	pll_set_post_language( $post_ids[ $lang ], $lang );
}
pll_save_post_translations( $post_ids );

// One English review with no translations yet (for "what is missing in Spanish?").
$single = get_posts( array( 'title' => 'A Bear Called Paddington', 'post_type' => 'post', 'post_status' => 'any', 'lang' => '', 'numberposts' => 1 ) );
if ( ! $single ) {
	$single_id = wp_insert_post(
		array(
			'post_title'    => 'A Bear Called Paddington',
			'post_status'   => 'publish',
			'post_type'     => 'post',
			'post_content'  => "<!-- wp:paragraph -->\n<p>An untranslated fixture review. Emoji survive: 🐻📚</p>\n<!-- /wp:paragraph -->",
			'post_category' => array( $category_ids['en'] ),
		)
	);
	pll_set_post_language( $single_id, 'en' );
}

// One menu per language on a "primary" location (Polylang's per-language assignment), with
// a page link and Polylang's language switcher. Block themes register no locations, so the
// assignment is written to Polylang's option directly, which is what the bridge reads.
$menu_ids = array();
foreach ( array( 'en', 'es' ) as $lang ) {
	$name = "wpmcp dev menu $lang";
	$menu = wp_get_nav_menu_object( $name );
	if ( ! $menu ) {
		$menu_id = wp_create_nav_menu( $name );
		wp_update_nav_menu_item(
			$menu_id,
			0,
			array(
				'menu-item-type'      => 'post_type',
				'menu-item-object'    => 'post',
				'menu-item-object-id' => $post_ids[ $lang ],
				'menu-item-status'    => 'publish',
			)
		);
		$switcher = wp_update_nav_menu_item(
			$menu_id,
			0,
			array(
				'menu-item-type'   => 'custom',
				'menu-item-object' => 'custom',
				'menu-item-url'    => '#pll_switcher',
				'menu-item-title'  => 'Languages',
				'menu-item-status' => 'publish',
			)
		);
		update_post_meta( $switcher, '_pll_menu_item', array( 'hide_if_no_translation' => 0, 'hide_current' => 0, 'force_home' => 0, 'show_flags' => 0, 'show_names' => 1, 'dropdown' => 0 ) );
		$menu = wp_get_nav_menu_object( $menu_id );
	}
	$menu_ids[ $lang ] = (int) $menu->term_id;
}
$options = get_option( 'polylang' );
$options['nav_menus'][ get_option( 'stylesheet' ) ]['primary'] = $menu_ids;
update_option( 'polylang', $options );

WP_CLI::success(
	sprintf(
		'languages: %s; categories: %s; posts: %s; menus: %s',
		implode( ',', pll_languages_list() ),
		wp_json_encode( $category_ids ),
		wp_json_encode( $post_ids ),
		wp_json_encode( $menu_ids )
	)
);
