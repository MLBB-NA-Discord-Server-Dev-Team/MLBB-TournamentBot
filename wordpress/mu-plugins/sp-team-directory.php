<?php
/**
 * SportsPress Team Directory Shortcode
 *
 * Registers [sp_team_directory] — lists all published sp_team posts
 * using the same gallery markup as SportsPress team-gallery.php so
 * Alchemists theme CSS applies without any extra styling.
 *
 * Usage: [sp_team_directory columns="4" orderby="title" order="ASC"]
 */

if ( ! defined( 'ABSPATH' ) ) exit;

add_shortcode( 'sp_team_directory', 'mlbb_sp_team_directory_shortcode' );

function mlbb_sp_team_directory_shortcode( $atts ) {
    $atts = shortcode_atts( array(
        'columns'  => 4,
        'orderby'  => 'title',
        'order'    => 'ASC',
        'number'   => -1,
    ), $atts, 'sp_team_directory' );

    $query = new WP_Query( array(
        'post_type'      => 'sp_team',
        'post_status'    => 'publish',
        'posts_per_page' => intval( $atts['number'] ),
        'orderby'        => sanitize_key( $atts['orderby'] ),
        'order'          => strtoupper( $atts['order'] ) === 'DESC' ? 'DESC' : 'ASC',
        'no_found_rows'  => true,
    ) );

    if ( ! $query->have_posts() ) {
        return '<p>No teams found.</p>';
    }

    $columns   = max( 1, intval( $atts['columns'] ) );
    $itemwidth = floor( 100 / $columns );
    $selector  = 'sp-team-directory-all';
    $float     = is_rtl() ? 'right' : 'left';

    $output  = "\n\t\t\t\t<style type='text/css'>";
    $output .= "#{$selector} { margin: auto; } ";
    $output .= "#{$selector} .gallery-item { float: {$float}; margin-top: 10px; text-align: center; width: {$itemwidth}%; } ";
    $output .= "#{$selector} .gallery-item a { display:block; } ";
    $output .= "#{$selector} img { border: 0 none; } ";
    $output .= "#{$selector} .gallery-caption { margin-left: 0; font-weight: 600; } ";
    $output .= "#{$selector}::after { content:''; display:table; clear:both; } ";
    $output .= "</style>\n";

    $output .= "<div id='{$selector}' class='gallery galleryid-0 gallery-columns-{$columns} gallery-size-sportspress-crop-medium'>";

    $link_teams = get_option( 'sportspress_link_teams', 'yes' ) === 'yes';

    while ( $query->have_posts() ) {
        $query->the_post();
        $team_id   = get_the_ID();
        $team_name = get_the_title();
        $permalink = get_permalink();

        if ( has_post_thumbnail( $team_id ) ) {
            $thumbnail = get_the_post_thumbnail( $team_id, 'sportspress-fit-medium' );
        } else {
            $thumbnail = '<img width="150" height="150" src="https://www.gravatar.com/avatar/?s=150&d=blank&f=y" class="attachment-thumbnail wp-post-image" alt="' . esc_attr( $team_name ) . '">';
        }

        $caption = '<dd class="wp-caption-text gallery-caption small-3 columns">';
        if ( $link_teams ) {
            $caption .= '<a href="' . esc_url( $permalink ) . '">' . esc_html( $team_name ) . '</a>';
        } else {
            $caption .= esc_html( $team_name );
        }
        $caption .= '</dd>';

        $output .= "<dl class='gallery-item'>";
        $output .= "<dt class='gallery-icon portrait'>";
        $output .= '<a href="' . esc_url( $permalink ) . '">' . $thumbnail . '</a>';
        $output .= "</dt>";
        $output .= $caption;
        $output .= "</dl>";
    }

    wp_reset_postdata();

    $output .= "</div>\n";

    return $output;
}
