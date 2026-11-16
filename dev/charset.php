<?php
/**
 * Sites installed years ago often still have their WordPress core tables in utf8mb3:
 * WordPress then stores emoji in post titles, excerpts and content as HTML entities, and
 * post meta and term names cannot hold them at all. This stack does the same, so that the
 * stricter case is the one tested. Idempotent; runs before provision.php, in its own process (WordPress
 * caches column charsets per request).
 *
 * Run with: bin/dev wp eval-file /dev-scripts/charset.php
 */

global $wpdb;

$tables = array( $wpdb->posts, $wpdb->postmeta, $wpdb->terms, $wpdb->termmeta, $wpdb->term_taxonomy, $wpdb->options, $wpdb->users, $wpdb->usermeta );
foreach ( $tables as $table ) {
	$charset = $wpdb->get_var(
		$wpdb->prepare(
			'SELECT c.character_set_name FROM information_schema.TABLES t JOIN information_schema.COLLATION_CHARACTER_SET_APPLICABILITY c ON c.collation_name = t.table_collation WHERE t.table_schema = DATABASE() AND t.table_name = %s',
			$table
		)
	);
	if ( 'utf8mb3' === $charset ) {
		continue;
	}
	if ( $wpdb->posts === $table ) {
		// Raw 4-byte characters cannot be converted: store them as WordPress would have.
		foreach ( $wpdb->get_results( "SELECT ID, post_title, post_content, post_excerpt FROM $wpdb->posts" ) as $row ) {
			$wpdb->update(
				$wpdb->posts,
				array(
					'post_title'   => wp_encode_emoji( $row->post_title ),
					'post_content' => wp_encode_emoji( $row->post_content ),
					'post_excerpt' => wp_encode_emoji( $row->post_excerpt ),
				),
				array( 'ID' => $row->ID )
			);
		}
	}
	if ( false === $wpdb->query( "ALTER TABLE $table CONVERT TO CHARACTER SET utf8mb3 COLLATE utf8mb3_unicode_ci" ) ) {
		WP_CLI::error( "could not convert $table: {$wpdb->last_error} (bin/dev reset gives a clean stack)" );
	}
	WP_CLI::log( "$table: utf8mb3" );
}
WP_CLI::success( 'core tables are utf8mb3, as on older installs' );
