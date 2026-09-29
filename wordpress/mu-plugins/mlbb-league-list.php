<?php
/**
 * Plugin Name: MLBB League List
 * Description: [mlbb_league_list] shortcode — live league registration table from mlbb_registration_periods.
 *
 * Attributes:
 *   rule="DPBO5"     — exact or prefix match (DP, Brawl, DPBO5, BrawlBO3 …)
 *   search="Moniyan" — LIKE match on league title (individual league pages)
 *   custom="1"       — only leagues created via /league create (mlbb_is_custom termmeta)
 *   status="open"    — open | scheduled | all  (default: open+scheduled)
 *   limit="50"       — max rows
 *
 * Rule is sourced from mlbb_registration_periods.rule (denormalized copy of wp_termmeta mlbb_rule).
 * The termmeta is the authoritative source; season_init.py populates the periods column automatically.
 */

// Register sp_league termmeta so it's accessible via WP REST API
add_action( 'init', function () {
    foreach ( [ 'mlbb_rule', 'mlbb_is_custom' ] as $key ) {
        register_term_meta( 'sp_league', $key, [
            'show_in_rest'  => true,
            'single'        => true,
            'type'          => 'string',
            'auth_callback' => function () { return current_user_can( 'manage_options' ); },
        ] );
    }
} );

add_shortcode( 'mlbb_league_list', 'mlbb_league_list_render' );

function mlbb_league_list_render( $atts ) {
    $atts = shortcode_atts( [
        'rule'   => '',
        'search' => '',
        'custom' => '',
        'status' => '',
        'limit'  => 50,
    ], $atts );

    global $wpdb;

    $where  = [ "rp.entity_type = 'league'", "p.post_status = 'publish'" ];
    $joins  = [ "JOIN {$wpdb->posts} p ON p.ID = rp.entity_id" ];
    $params = [];

    // Status filter
    if ( $atts['status'] === 'open' ) {
        $where[] = "rp.status = 'open'";
    } elseif ( $atts['status'] === 'scheduled' ) {
        $where[] = "rp.status = 'scheduled'";
    } elseif ( $atts['status'] === 'all' ) {
        $where[] = "rp.status IN ('open','scheduled','closed')";
    } else {
        $where[] = "rp.status IN ('open','scheduled')";
    }

    // Rule filter — exact value or prefix (DP, Brawl)
    if ( $atts['rule'] !== '' ) {
        $where[]  = 'rp.rule LIKE %s';
        $params[] = $wpdb->esc_like( $atts['rule'] ) . '%';
    }

    // Title search
    if ( $atts['search'] !== '' ) {
        $where[]  = 'p.post_title LIKE %s';
        $params[] = '%' . $wpdb->esc_like( $atts['search'] ) . '%';
    }

    // Custom leagues filter — join through sp_league termmeta
    if ( $atts['custom'] === '1' ) {
        $joins[] = "JOIN {$wpdb->term_relationships} wtr ON wtr.object_id = p.ID";
        $joins[] = "JOIN {$wpdb->term_taxonomy} wtt ON wtt.term_taxonomy_id = wtr.term_taxonomy_id AND wtt.taxonomy = 'sp_league'";
        $joins[] = "JOIN {$wpdb->termmeta} tm_custom ON tm_custom.term_id = wtt.term_id AND tm_custom.meta_key = 'mlbb_is_custom' AND tm_custom.meta_value = '1'";
    }

    $joins_sql = implode( "\n        ", $joins );
    $where_sql = implode( ' AND ', $where );
    $limit     = max( 1, intval( $atts['limit'] ) );
    $params[]  = $limit;

    $sql = $wpdb->prepare(
        "SELECT
            p.ID,
            p.post_title,
            p.post_name,
            COALESCE(rp.rule, tm_rule.meta_value) AS rule,
            rp.status,
            rp.opens_at,
            rp.closes_at,
            rp.max_teams,
            (SELECT COUNT(*) FROM mlbb_team_registrations tr
             WHERE tr.period_id = rp.id AND tr.status != 'rejected') AS team_count
         FROM mlbb_registration_periods rp
         {$joins_sql}
         LEFT JOIN {$wpdb->term_relationships} wtr2 ON wtr2.object_id = p.ID
         LEFT JOIN {$wpdb->term_taxonomy} wtt2 ON wtt2.term_taxonomy_id = wtr2.term_taxonomy_id AND wtt2.taxonomy = 'sp_league'
         LEFT JOIN {$wpdb->termmeta} tm_rule ON tm_rule.term_id = wtt2.term_id AND tm_rule.meta_key = 'mlbb_rule'
         WHERE {$where_sql}
         GROUP BY rp.id
         ORDER BY
             FIELD(rp.status,'open','scheduled','closed'),
             rp.opens_at ASC,
             p.post_title ASC
         LIMIT %d",
        $params
    );

    $rows = $wpdb->get_results( $sql );

    if ( empty( $rows ) ) {
        return '<p class="mlbb-league-empty">No leagues are currently open or scheduled for registration.</p>';
    }

    $rule_labels = [
        'DPBO1'    => 'Draft Pick &middot; BO1',
        'DPBO3'    => 'Draft Pick &middot; BO3',
        'DPBO5'    => 'Draft Pick &middot; BO5',
        'BrawlBO1' => 'Brawl &middot; BO1',
        'BrawlBO3' => 'Brawl &middot; BO3',
        'BrawlBO5' => 'Brawl &middot; BO5',
        'FreePlay' => 'Free Play',
    ];

    $now = current_time( 'timestamp' );

    ob_start();
    echo mlbb_league_list_css();
    ?>
    <div class="mlbb-league-list">
        <table class="mlbb-league-table">
            <thead>
                <tr>
                    <th>League</th>
                    <th>Format</th>
                    <th>Closes</th>
                    <th>Teams</th>
                    <th>Status</th>
                </tr>
            </thead>
            <tbody>
            <?php foreach ( $rows as $row ) :
                // Derive franchise page URL by stripping season suffix from sp_table slug
                $franchise_slug = preg_replace( '/-(?:spring|summer|fall|winter)-\d{4}(?:-\d+)?$/i', '', $row->post_name );
                $franchise_page = get_page_by_path( $franchise_slug, OBJECT, 'page' );
                $league_url     = $franchise_page ? get_permalink( $franchise_page->ID ) : get_permalink( $row->ID );

                // Split "Moniyan League — Spring 2026" into name + season
                $parts       = preg_split( '/\s+(?:—|–|-)+\s+/', $row->post_title, 2 );
                $league_name = $parts[0];
                $season      = isset( $parts[1] ) ? $parts[1] : '';

                $label  = isset( $rule_labels[ $row->rule ] ) ? $rule_labels[ $row->rule ] : esc_html( $row->rule );
                $closes = $row->closes_at ? strtotime( $row->closes_at ) : null;
                $opens  = $row->opens_at  ? strtotime( $row->opens_at  ) : null;
                $cap    = $row->max_teams ? (int) $row->max_teams : null;
                $teams  = (int) $row->team_count;
                $teams_display = $cap ? $teams . ' / ' . $cap : $teams;

                if ( $row->status === 'open' && $closes ) {
                    $days_left   = (int) ceil( ( $closes - $now ) / DAY_IN_SECONDS );
                    $status_html = '<span class="mlbb-badge mlbb-badge--open">Open</span>';
                    $closes_html = esc_html( date_i18n( 'M j', $closes ) ) . ' <small>(' . $days_left . 'd)</small>';
                } elseif ( $row->status === 'scheduled' && $opens ) {
                    $days_until  = (int) ceil( ( $opens - $now ) / DAY_IN_SECONDS );
                    $status_html = '<span class="mlbb-badge mlbb-badge--scheduled">Opens in ' . $days_until . 'd</span>';
                    $closes_html = $closes ? esc_html( date_i18n( 'M j', $closes ) ) : '—';
                } else {
                    $status_html = '<span class="mlbb-badge mlbb-badge--closed">Closed</span>';
                    $closes_html = $closes ? esc_html( date_i18n( 'M j', $closes ) ) : '—';
                }
            ?>
                <tr>
                    <td class="mlbb-league-name">
                        <a href="<?php echo esc_url( $league_url ); ?>"><?php echo esc_html( $league_name ); ?></a>
                        <?php if ( $season ) : ?><br><small class="mlbb-season"><?php echo esc_html( $season ); ?></small><?php endif; ?>
                    </td>
                    <td><?php echo $label; ?></td>
                    <td><?php echo $closes_html; ?></td>
                    <td><?php echo esc_html( $teams_display ); ?></td>
                    <td><?php echo $status_html; ?></td>
                </tr>
            <?php endforeach; ?>
            </tbody>
        </table>
    </div>
    <?php
    return ob_get_clean();
}

// ── [mlbb_league_register_help] ──────────────────────────────────────────────
// Shows a "How to Register" box with the dynamic league_id for the current page.
// Usage: [mlbb_league_register_help search="Moniyan"]
//   search — same LIKE match used in mlbb_league_list to find the league on this page

add_shortcode( 'mlbb_league_register_help', 'mlbb_league_register_help_render' );

function mlbb_league_register_help_render( $atts ) {
    $atts = shortcode_atts( [ 'search' => '' ], $atts );

    global $wpdb;

    $league_id = null;

    $rule = null;
    if ( $atts['search'] !== '' ) {
        $rows = $wpdb->get_results( $wpdb->prepare(
            "SELECT rp.entity_id, rp.rule
             FROM mlbb_registration_periods rp
             JOIN {$wpdb->posts} p ON p.ID = rp.entity_id
             WHERE rp.entity_type = 'league'
               AND p.post_status = 'publish'
               AND rp.status IN ('open','scheduled')
               AND p.post_title LIKE %s
             ORDER BY FIELD(rp.status,'open','scheduled'), rp.opens_at ASC
             LIMIT 1",
            '%' . $wpdb->esc_like( $atts['search'] ) . '%'
        ) );
        if ( ! empty( $rows ) ) {
            $league_id = (int) $rows[0]->entity_id;
            $rule = $rows[0]->rule;
        }
    }

    $is_free_play = ( $rule === 'FreePlay' );

    if ( $league_id ) {
        $cmd = '<code>/league register league_id:' . $league_id . '</code>';
    } else {
        $cmd = '<code>/league register league_id:<em>ID</em></code>';
    }

    ob_start();
    echo mlbb_register_help_css();
    ?>
    <div class="card mlbb-register-card">
        <div class="card__header">
            <h4>How to Register</h4>
        </div>
        <div class="card__content">
            <?php if ( $is_free_play ): ?>
            <ol class="mlbb-register-steps">
                <li>Join the <a href="https://discord.gg/mlbbna" target="_blank" rel="noopener">NA Discord Server</a></li>
                <li>Use <code>/player register</code> to link your MLBB profile</li>
                <li>Type <?php echo $cmd; ?> in any bot channel to enter the pool</li>
            </ol>
            <div class="mlbb-register-note">
                <i class="fas fa-info-circle"></i>
                <strong>Free Play:</strong> This is a <em>solo signup</em> league. No team needed &mdash; just sign up as an individual and the bot will <strong>randomly assign you to a team</strong> when registration closes. Want out before then? Use <code>/league leave-free-play league_id:<?php echo $league_id ?: 'ID'; ?></code>.
            </div>
            <?php else: ?>
            <ol class="mlbb-register-steps">
                <li>Join the <a href="https://discord.gg/mlbbna" target="_blank" rel="noopener">NA Discord Server</a></li>
                <li>Make sure you have a team &mdash; use <code>/team create</code> if you don't</li>
                <li>Type <?php echo $cmd; ?> in any bot channel</li>
            </ol>
            <div class="mlbb-register-note">
                <i class="fas fa-info-circle"></i>
                <strong>Captaining multiple teams?</strong> Add <code>team_id:<em>your_team_id</em></code> to specify which team to register. Use <code>/team list</code> to find your team IDs.
            </div>
            <?php endif; ?>
        </div>
    </div>
    <?php
    return ob_get_clean();
}

function mlbb_register_help_css() {
    static $printed = false;
    if ( $printed ) return '';
    $printed = true;
    return '
<style>
.mlbb-register-card { margin-top: 1.5em; }
.mlbb-register-steps {
    margin: 0 0 16px; padding-left: 1.4em;
}
.mlbb-register-steps li { margin-bottom: 6px; }
.mlbb-register-steps code,
.mlbb-register-note code {
    background: rgba(255,255,255,0.08); padding: 2px 6px;
    border-radius: 3px; font-size: 0.88em; color: #00ff5b;
}
.mlbb-register-note {
    padding: 10px 14px;
    background: rgba(255,255,255,0.04);
    border: 1px solid #4b3b60;
    border-radius: 4px;
    font-size: 0.88em;
    color: rgba(255,255,255,0.6);
}
.mlbb-register-note i {
    margin-right: 4px; color: #00ff5b;
}
.mlbb-register-note code { color: #e8d96e; }
</style>';
}

function mlbb_league_list_css() {
    static $printed = false;
    if ( $printed ) return '';
    $printed = true;
    return '
<style>
.mlbb-league-list { overflow-x: auto; margin: 1.5em 0; }
.mlbb-league-table {
    width: 100%; border-collapse: collapse;
    font-size: 0.9em; background: rgba(0,0,0,0.25);
    border-radius: 6px; overflow: hidden;
}
.mlbb-league-table thead tr { background: rgba(255,255,255,0.08); }
.mlbb-league-table th {
    padding: 10px 14px; text-align: left;
    font-size: 0.75em; text-transform: uppercase;
    letter-spacing: 0.08em; color: rgba(255,255,255,0.5);
    border-bottom: 1px solid rgba(255,255,255,0.1);
}
.mlbb-league-table td {
    padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06);
    color: rgba(255,255,255,0.85); vertical-align: middle;
}
.mlbb-league-table tbody tr:last-child td { border-bottom: none; }
.mlbb-league-table tbody tr:hover td { background: rgba(255,255,255,0.04); }
.mlbb-league-name a { color: #fff; font-weight: 600; text-decoration: none; }
.mlbb-league-name a:hover { color: #FFB703; }
.mlbb-season { color: rgba(255,255,255,0.45); font-size: 0.85em; }
.mlbb-league-table small { color: rgba(255,255,255,0.45); }
.mlbb-badge {
    display: inline-block; padding: 2px 8px; border-radius: 3px;
    font-size: 0.75em; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase;
}
.mlbb-badge--open      { background: #1a6b32; color: #6ee89a; }
.mlbb-badge--scheduled { background: #3a3a10; color: #e8d96e; }
.mlbb-badge--closed    { background: rgba(255,255,255,0.08); color: rgba(255,255,255,0.4); }
.mlbb-league-empty { color: rgba(255,255,255,0.5); font-style: italic; }
</style>';
}


// ── [mlbb_league_seasons] ──────────────────────────────────────────────────
// Lists all seasons for a league franchise with standings table embedded for
// the most recent season. Usage: [mlbb_league_seasons search="Moniyan"]
add_shortcode( 'mlbb_league_seasons', 'mlbb_league_seasons_render' );

function mlbb_league_seasons_render( $atts ) {
    $atts = shortcode_atts( [
        'search' => '',
        'show_current' => '1',
    ], $atts );

    global $wpdb;

    if ( $atts['search'] === '' ) {
        return '<p class="mlbb-league-empty">No league specified.</p>';
    }

    // Find all sp_table posts matching the search, one per season.
    // Join mlbb_season_schedule via the season taxonomy so we can order by
    // actual play_start date and identify the currently in-progress season.
    $rows = $wpdb->get_results( $wpdb->prepare(
        "SELECT p.ID, p.post_title, p.post_name, p.post_date,
                ss.play_start, ss.play_end
         FROM {$wpdb->posts} p
         LEFT JOIN {$wpdb->term_relationships} wtr ON wtr.object_id = p.ID
         LEFT JOIN {$wpdb->term_taxonomy} wtt
             ON wtt.term_taxonomy_id = wtr.term_taxonomy_id AND wtt.taxonomy = 'sp_season'
         LEFT JOIN {$wpdb->terms} t ON t.term_id = wtt.term_id
         LEFT JOIN mlbb_season_schedule ss ON ss.season_name = t.name COLLATE utf8mb4_unicode_520_ci
         WHERE p.post_type = 'sp_table' AND p.post_status = 'publish'
           AND p.post_title LIKE %s
         GROUP BY p.ID
         ORDER BY ss.play_start ASC, p.post_date ASC",
        '%' . $wpdb->esc_like( $atts['search'] ) . '%'
    ) );

    if ( empty( $rows ) ) {
        return '<p class="mlbb-league-empty">No seasons found for this league.</p>';
    }

    ob_start();
    echo mlbb_league_seasons_css();

    // Show the currently in-progress season's standings at the top.
    // Fall back to the most recently completed season, then to the next upcoming.
    if ( $atts['show_current'] === '1' && ! empty( $rows ) ) {
        $today_ts = current_time( 'timestamp' );
        $in_progress = null;
        $last_complete = null;
        $next_upcoming = null;
        foreach ( $rows as $r ) {
            if ( ! $r->play_start || ! $r->play_end ) continue;
            $ps = strtotime( $r->play_start );
            $pe = strtotime( $r->play_end );
            if ( $today_ts >= $ps && $today_ts <= $pe ) {
                $in_progress = $r;
                break;
            } elseif ( $today_ts > $pe ) {
                if ( $last_complete === null || strtotime( $last_complete->play_end ) < $pe ) {
                    $last_complete = $r;
                }
            } elseif ( $today_ts < $ps ) {
                if ( $next_upcoming === null || strtotime( $next_upcoming->play_start ) > $ps ) {
                    $next_upcoming = $r;
                }
            }
        }
        $current = $in_progress ?: ( $last_complete ?: ( $next_upcoming ?: $rows[0] ) );

        if ( $in_progress ) {
            $heading_label = 'Current Standings';
        } elseif ( $last_complete === $current ) {
            $heading_label = 'Final Standings';
        } else {
            $heading_label = 'Upcoming Season';
        }

        echo '<div class="mlbb-current-standings">';
        echo '<h3>' . esc_html( $current->post_title ) . ' &mdash; ' . esc_html( $heading_label ) . '</h3>';
        echo do_shortcode( '[league_table id="' . intval( $current->ID ) . '"]' );
        echo '</div>';
    }
    ?>
    <h3 class="mlbb-seasons-heading">All Seasons</h3>
    <div class="mlbb-seasons-list">
        <table class="mlbb-seasons-table">
            <thead>
                <tr>
                    <th>Season</th>
                    <th>Format</th>
                    <th>Registration</th>
                    <th>Teams</th>
                    <th>Status</th>
                </tr>
            </thead>
            <tbody>
            <?php
            $rule_labels = [
                'DPBO1'    => 'Draft Pick &middot; BO1',
                'DPBO3'    => 'Draft Pick &middot; BO3',
                'DPBO5'    => 'Draft Pick &middot; BO5',
                'BrawlBO1' => 'Brawl &middot; BO1',
                'BrawlBO3' => 'Brawl &middot; BO3',
                'BrawlBO5' => 'Brawl &middot; BO5',
                'FreePlay' => 'Free Play',
            ];
            foreach ( $rows as $row ):
                $parts  = preg_split( '/\s+(?:—|–|-)+\s+/', $row->post_title, 2 );
                $season = isset( $parts[1] ) ? $parts[1] : $row->post_title;

                $schedule = $wpdb->get_row( $wpdb->prepare(
                    "SELECT play_start, play_end, reg_opens, reg_closes
                     FROM mlbb_season_schedule WHERE season_name COLLATE utf8mb4_unicode_520_ci = %s",
                    $season
                ) );

                $today_ts = current_time( 'timestamp' );
                $play_status = 'unknown';
                if ( $schedule ) {
                    $ps = strtotime( $schedule->play_start );
                    $pe = strtotime( $schedule->play_end );
                    if ( $today_ts < $ps ) $play_status = 'upcoming';
                    elseif ( $today_ts <= $pe ) $play_status = 'in_progress';
                    else $play_status = 'complete';
                }

                $reg = $wpdb->get_row( $wpdb->prepare(
                    "SELECT rp.id, rp.status, rp.opens_at, rp.closes_at, rp.max_teams, rp.rule,
                            (SELECT COUNT(*) FROM mlbb_team_registrations tr
                             WHERE tr.period_id = rp.id AND tr.status != 'rejected') AS team_count
                     FROM mlbb_registration_periods rp
                     WHERE rp.entity_type='league' AND rp.entity_id = %d
                     ORDER BY rp.id DESC LIMIT 1",
                    $row->ID
                ) );

                $rule  = $reg ? $reg->rule : '';
                $label = isset( $rule_labels[ $rule ] ) ? $rule_labels[ $rule ] : ( $rule ?: '&mdash;' );

                $teams_display = '&mdash;';
                if ( $reg ) {
                    $cap = $reg->max_teams ? (int) $reg->max_teams : null;
                    $teams_display = $cap ? ( (int) $reg->team_count . ' / ' . $cap ) : (int) $reg->team_count;
                }

                $reg_display = '&mdash;';
                if ( $reg && $reg->opens_at ) {
                    $opens  = strtotime( $reg->opens_at );
                    $closes = $reg->closes_at ? strtotime( $reg->closes_at ) : null;
                    if ( $reg->status === 'open' && $closes ) {
                        $days_left = max( 0, (int) ceil( ( $closes - $today_ts ) / DAY_IN_SECONDS ) );
                        $reg_display = 'closes ' . esc_html( date_i18n( 'M j', $closes ) ) . ' <small>(' . $days_left . 'd left)</small>';
                    } elseif ( $reg->status === 'scheduled' ) {
                        $days_until = max( 0, (int) ceil( ( $opens - $today_ts ) / DAY_IN_SECONDS ) );
                        $reg_display = 'opens ' . esc_html( date_i18n( 'M j', $opens ) ) . ' <small>(in ' . $days_until . 'd)</small>';
                    } else {
                        $reg_display = 'closed';
                    }
                }

                if ( $reg && $reg->status === 'open' ) {
                    $status_html = '<span class="mlbb-badge mlbb-badge--open">Open</span>';
                } elseif ( $play_status === 'in_progress' ) {
                    $status_html = '<span class="mlbb-badge mlbb-badge--open">In Progress</span>';
                } elseif ( $reg && $reg->status === 'scheduled' && $reg->opens_at ) {
                    $days_until = max( 0, (int) ceil( ( strtotime( $reg->opens_at ) - $today_ts ) / DAY_IN_SECONDS ) );
                    $status_html = '<span class="mlbb-badge mlbb-badge--scheduled">Opens in ' . $days_until . 'd</span>';
                } elseif ( $play_status === 'upcoming' ) {
                    $status_html = '<span class="mlbb-badge mlbb-badge--scheduled">Coming Soon</span>';
                } else {
                    $status_html = '<span class="mlbb-badge mlbb-badge--closed">Closed</span>';
                }

                $table_url = get_permalink( $row->ID );
            ?>
                <tr class="mlbb-seasons-row" data-href="<?php echo esc_url( $table_url ); ?>">
                    <td class="mlbb-season-name">
                        <a href="<?php echo esc_url( $table_url ); ?>"><?php echo esc_html( $season ); ?></a>
                    </td>
                    <td><?php echo $label; ?></td>
                    <td><?php echo $reg_display; ?></td>
                    <td><?php echo $teams_display; ?></td>
                    <td><?php echo $status_html; ?></td>
                </tr>
            <?php endforeach; ?>
            </tbody>
        </table>
        <script>
        (function(){
            var rows = document.querySelectorAll('.mlbb-seasons-row[data-href]');
            rows.forEach(function(r){
                r.style.cursor = 'pointer';
                r.addEventListener('click', function(e){
                    if (e.target.tagName === 'A') return;
                    window.location.href = r.getAttribute('data-href');
                });
            });
        })();
        </script>
    </div>
    <?php
    return ob_get_clean();
}

function mlbb_league_seasons_css() {
    static $printed = false;
    if ( $printed ) return '';
    $printed = true;
    return '
<style>
.mlbb-current-standings { margin: 1.5em 0; }
.mlbb-current-standings h3 { margin-bottom: 0.5em; color: #fff; }
.mlbb-seasons-heading { margin-top: 2em; color: #fff; }
.mlbb-seasons-list { overflow-x: auto; margin: 1em 0; }
.mlbb-seasons-table {
    width: 100%; border-collapse: collapse;
    font-size: 0.9em; background: rgba(0,0,0,0.25);
    border-radius: 6px; overflow: hidden;
}
.mlbb-seasons-table thead tr { background: rgba(255,255,255,0.08); }
.mlbb-seasons-table th {
    padding: 10px 14px; text-align: left;
    font-size: 0.75em; text-transform: uppercase;
    letter-spacing: 0.08em; color: rgba(255,255,255,0.5);
    border-bottom: 1px solid rgba(255,255,255,0.1);
}
.mlbb-seasons-table td {
    padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.06);
    color: rgba(255,255,255,0.85);
}
.mlbb-seasons-table tbody tr:last-child td { border-bottom: none; }
.mlbb-seasons-table tbody tr:hover td { background: rgba(255,255,255,0.04); }
.mlbb-season-name a { color: #fff; font-weight: 600; text-decoration: none; }
.mlbb-season-name a:hover { color: #FFB703; }
.mlbb-seasons-table small { color: rgba(255,255,255,0.45); }
.mlbb-seasons-row:hover td { background: rgba(255,255,255,0.04); }
.mlbb-badge {
    display: inline-block; padding: 2px 8px; border-radius: 3px;
    font-size: 0.75em; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase;
}
.mlbb-badge--open      { background: #1a6b32; color: #6ee89a; }
.mlbb-badge--scheduled { background: #3a3a10; color: #e8d96e; }
.mlbb-badge--closed    { background: rgba(255,255,255,0.08); color: rgba(255,255,255,0.4); }
</style>';
}


// ── [mlbb_season_leagues] ──────────────────────────────────────────────────
// Lists all leagues (sp_tables) that ran in a given sp_season term, with
// their current standings and links. Used by sp_season archive pages.
// Usage: [mlbb_season_leagues season="spring-2026"]
add_shortcode( 'mlbb_season_leagues', 'mlbb_season_leagues_render' );

function mlbb_season_leagues_render( $atts ) {
    $atts = shortcode_atts( [
        'season' => '',  // season slug or name
    ], $atts );

    global $wpdb;

    // Resolve season term
    $season_term = null;
    if ( $atts['season'] !== '' ) {
        $season_term = get_term_by( 'slug', $atts['season'], 'sp_season' );
        if ( ! $season_term ) {
            $season_term = get_term_by( 'name', $atts['season'], 'sp_season' );
        }
    } elseif ( is_tax( 'sp_season' ) ) {
        $season_term = get_queried_object();
    }

    if ( ! $season_term ) {
        return '<p class="mlbb-league-empty">Season not found.</p>';
    }

    // Find all sp_tables associated with this season term
    $tables = $wpdb->get_results( $wpdb->prepare(
        "SELECT p.ID, p.post_title, p.post_name
         FROM {$wpdb->posts} p
         JOIN {$wpdb->term_relationships} wtr ON wtr.object_id = p.ID
         JOIN {$wpdb->term_taxonomy} wtt ON wtt.term_taxonomy_id = wtr.term_taxonomy_id
         WHERE p.post_type = 'sp_table'
           AND p.post_status = 'publish'
           AND wtt.taxonomy = 'sp_season'
           AND wtt.term_id = %d
         ORDER BY p.post_title",
        $season_term->term_id
    ) );

    // Also look up season metadata from mlbb_season_schedule
    $schedule = $wpdb->get_row( $wpdb->prepare(
        "SELECT play_start, play_end, reg_opens, reg_closes
         FROM mlbb_season_schedule WHERE season_name = %s",
        $season_term->name
    ) );

    ob_start();
    echo mlbb_season_leagues_css();
    ?>
    <div class="mlbb-season-overview">
        <?php if ( $schedule ) : ?>
        <div class="mlbb-season-dates">
            <div class="mlbb-season-dates__item">
                <span class="mlbb-season-dates__label">Registration</span>
                <span class="mlbb-season-dates__value"><?php echo esc_html( date( 'M j', strtotime( $schedule->reg_opens ) ) ); ?> – <?php echo esc_html( date( 'M j, Y', strtotime( $schedule->reg_closes ) ) ); ?></span>
            </div>
            <div class="mlbb-season-dates__item">
                <span class="mlbb-season-dates__label">Play Window</span>
                <span class="mlbb-season-dates__value"><?php echo esc_html( date( 'M j', strtotime( $schedule->play_start ) ) ); ?> – <?php echo esc_html( date( 'M j, Y', strtotime( $schedule->play_end ) ) ); ?></span>
            </div>
        </div>
        <?php endif; ?>

        <h2 class="mlbb-season-heading">Leagues this Season</h2>
        <?php if ( empty( $tables ) ) : ?>
            <p class="mlbb-league-empty">No leagues have been configured for this season yet.</p>
        <?php else : ?>
        <div class="mlbb-season-leagues-grid">
            <?php foreach ( $tables as $table ):
                // Derive franchise slug (strip season suffix)
                $franchise_slug = preg_replace( '/-(?:spring|summer|fall|winter)-\d{4}(?:-\d+)?$/i', '', $table->post_name );
                $franchise_page = get_page_by_path( $franchise_slug, OBJECT, 'page' );
                $franchise_url  = $franchise_page ? get_permalink( $franchise_page->ID ) : get_permalink( $table->ID );

                // Get league rule from termmeta
                $rule_row = $wpdb->get_var( $wpdb->prepare(
                    "SELECT tm.meta_value
                     FROM {$wpdb->term_relationships} wtr
                     JOIN {$wpdb->term_taxonomy} wtt
                         ON wtt.term_taxonomy_id = wtr.term_taxonomy_id AND wtt.taxonomy='sp_league'
                     JOIN {$wpdb->termmeta} tm ON tm.term_id = wtt.term_id AND tm.meta_key='mlbb_rule'
                     WHERE wtr.object_id = %d LIMIT 1",
                    $table->ID
                ) );

                $rule_labels = [
                    'DPBO1'    => 'Draft Pick BO1',
                    'DPBO3'    => 'Draft Pick BO3',
                    'DPBO5'    => 'Draft Pick BO5',
                    'BrawlBO1' => 'Brawl BO1',
                    'BrawlBO3' => 'Brawl BO3',
                    'BrawlBO5' => 'Brawl BO5',
                    'FreePlay' => 'Free Play',
                ];
                $rule_label = isset( $rule_labels[ $rule_row ] ) ? $rule_labels[ $rule_row ] : $rule_row;

                // Split title: "Moniyan League — Spring 2026" → "Moniyan League"
                $parts = preg_split( '/\s+(?:—|–|-)+\s+/', $table->post_title, 2 );
                $league_name = $parts[0];

                // Event counts for this specific league (sp_table) in this season.
                // Only used for the "X/Y matches played" display.
                $event_counts = $wpdb->get_row( $wpdb->prepare(
                    "SELECT
                        SUM(CASE WHEN p.post_status='publish' THEN 1 ELSE 0 END) AS played,
                        SUM(CASE WHEN p.post_status='future' THEN 1 ELSE 0 END) AS upcoming
                     FROM {$wpdb->posts} p
                     JOIN {$wpdb->term_relationships} wtr1 ON wtr1.object_id = p.ID
                     JOIN {$wpdb->term_taxonomy} wtt1
                         ON wtt1.term_taxonomy_id = wtr1.term_taxonomy_id AND wtt1.taxonomy='sp_season'
                     JOIN {$wpdb->term_relationships} wtr2 ON wtr2.object_id = p.ID
                     JOIN {$wpdb->term_taxonomy} wtt2
                         ON wtt2.term_taxonomy_id = wtr2.term_taxonomy_id AND wtt2.taxonomy='sp_league'
                     WHERE p.post_type='sp_event'
                       AND wtt1.term_id = %d
                       AND wtt2.term_id IN (
                           SELECT term_id FROM {$wpdb->term_taxonomy} tt
                           JOIN {$wpdb->term_relationships} tr ON tr.term_taxonomy_id = tt.term_taxonomy_id
                           WHERE tr.object_id = %d AND tt.taxonomy='sp_league'
                       )",
                    $season_term->term_id, $table->ID
                ) );
                $played   = intval( $event_counts->played ?? 0 );
                $upcoming = intval( $event_counts->upcoming ?? 0 );

                // Status driven by the season's play window, not event counts.
                $season_sched = $wpdb->get_row( $wpdb->prepare(
                    "SELECT play_start, play_end FROM mlbb_season_schedule WHERE season_name = %s",
                    $season_term->name
                ) );
                $today_ts = current_time( 'timestamp' );
                if ( $season_sched ) {
                    $ps = strtotime( $season_sched->play_start );
                    $pe = strtotime( $season_sched->play_end );
                    if ( $today_ts < $ps ) {
                        $badge = '<span class="mlbb-badge mlbb-badge--scheduled">Coming Soon</span>';
                    } elseif ( $today_ts >= $ps && $today_ts <= $pe ) {
                        $badge = '<span class="mlbb-badge mlbb-badge--open">In Progress</span>';
                    } else {
                        $badge = '<span class="mlbb-badge mlbb-badge--closed">Complete</span>';
                    }
                } else {
                    $badge = '<span class="mlbb-badge mlbb-badge--closed">Unscheduled</span>';
                }
            ?>
                <div class="mlbb-season-league-card">
                    <div class="mlbb-season-league-card__header">
                        <a href="<?php echo esc_url( $franchise_url ); ?>"><?php echo esc_html( $league_name ); ?></a>
                        <?php echo $badge; ?>
                    </div>
                    <div class="mlbb-season-league-card__meta">
                        <?php if ( $rule_label ): ?><span class="mlbb-season-league-card__rule"><?php echo esc_html( $rule_label ); ?></span><?php endif; ?>
                        <span class="mlbb-season-league-card__stats"><?php echo $played; ?>/<?php echo ( $played + $upcoming ); ?> matches played</span>
                    </div>
                    <div class="mlbb-season-league-card__actions">
                        <a href="<?php echo esc_url( get_permalink( $table->ID ) ); ?>" class="mlbb-season-league-card__btn">View Standings &raquo;</a>
                    </div>
                </div>
            <?php endforeach; ?>
        </div>
        <?php endif; ?>
    </div>
    <?php
    return ob_get_clean();
}

function mlbb_season_leagues_css() {
    static $printed = false;
    if ( $printed ) return '';
    $printed = true;
    return '
<style>
.mlbb-season-overview { margin: 1.5em 0; }
.mlbb-season-dates {
    display: flex; flex-wrap: wrap; gap: 2em;
    padding: 16px 20px;
    background: rgba(0,0,0,0.25);
    border-radius: 6px;
    margin-bottom: 1.5em;
}
.mlbb-season-dates__item { display: flex; flex-direction: column; }
.mlbb-season-dates__label {
    font-size: 0.7em; text-transform: uppercase;
    letter-spacing: 0.08em; color: rgba(255,255,255,0.5);
}
.mlbb-season-dates__value { color: #fff; font-weight: 600; font-size: 1em; }

.mlbb-season-heading { color: #fff; margin: 1em 0 0.8em; }

.mlbb-season-leagues-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 1em;
}

.mlbb-season-league-card {
    padding: 14px 18px;
    background: rgba(0,0,0,0.35);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 6px;
    display: flex; flex-direction: column; gap: 10px;
}
.mlbb-season-league-card__header {
    display: flex; justify-content: space-between; align-items: center; gap: 10px;
}
.mlbb-season-league-card__header a {
    color: #fff; font-weight: 700; text-decoration: none; font-size: 1em;
}
.mlbb-season-league-card__header a:hover { color: #FFB703; }

.mlbb-season-league-card__meta {
    display: flex; gap: 12px; font-size: 0.8em; color: rgba(255,255,255,0.65);
}
.mlbb-season-league-card__rule {
    padding: 2px 8px; background: rgba(0,255,91,0.12);
    color: #00ff5b; border-radius: 3px; font-weight: 600;
}

.mlbb-season-league-card__actions a {
    color: #00ff5b; font-size: 0.85em; text-decoration: none;
}
.mlbb-season-league-card__actions a:hover { text-decoration: underline; }
</style>';
}

// ── Auto-inject into sp_season taxonomy archives ──────────────────────────
// The Alchemists theme only calls category_description()/tag_description()
// in archive.php, never term_description() for custom taxonomies. So we hook
// into loop_start and echo the season leagues content before the post loop.
add_action( 'loop_start', function( $query ) {
    if ( ! is_admin() && $query->is_main_query() && is_tax( 'sp_season' ) ) {
        // Only inject once per page load
        static $injected = false;
        if ( $injected ) return;
        $injected = true;

        $term = get_queried_object();
        if ( $term && isset( $term->slug ) ) {
            echo '<div class="mlbb-season-archive-header container">';
            echo do_shortcode( '[mlbb_season_leagues season="' . esc_attr( $term->slug ) . '"]' );
            echo '</div>';
        }
    }
}, 5 );


// ── [mlbb_all_seasons] ─────────────────────────────────────────────────────
// Grid of all configured seasons with dates and a link to each season archive.
// Used on the /seasons/ hub page.
add_shortcode( 'mlbb_all_seasons', 'mlbb_all_seasons_render' );

function mlbb_all_seasons_render( $atts ) {
    global $wpdb;

    // Pull all seasons with their schedule info, ordered by play_start
    $rows = $wpdb->get_results(
        "SELECT ss.sp_season_id, ss.season_name, ss.play_start, ss.play_end,
                ss.reg_opens, ss.reg_closes,
                t.slug
         FROM mlbb_season_schedule ss
         LEFT JOIN {$wpdb->terms} t ON t.term_id = ss.sp_season_id
         ORDER BY ss.play_start DESC"
    );

    if ( empty( $rows ) ) {
        return '<p class="mlbb-league-empty">No seasons configured yet.</p>';
    }

    $today = current_time( 'timestamp' );

    ob_start();
    echo mlbb_all_seasons_css();
    ?>
    <div class="mlbb-all-seasons">
        <?php foreach ( $rows as $row ):
            $play_start = strtotime( $row->play_start );
            $play_end   = strtotime( $row->play_end );
            $reg_opens  = strtotime( $row->reg_opens );
            $reg_closes = strtotime( $row->reg_closes );

            if ( $today < $reg_opens ) {
                $status = '<span class="mlbb-badge mlbb-badge--scheduled">Upcoming</span>';
            } elseif ( $today >= $reg_opens && $today < $reg_closes ) {
                $status = '<span class="mlbb-badge mlbb-badge--open">Registration Open</span>';
            } elseif ( $today >= $play_start && $today <= $play_end ) {
                $status = '<span class="mlbb-badge mlbb-badge--open">In Play</span>';
            } else {
                $status = '<span class="mlbb-badge mlbb-badge--closed">Complete</span>';
            }

            // Count leagues for this season
            $league_count = (int) $wpdb->get_var( $wpdb->prepare(
                "SELECT COUNT(*) FROM {$wpdb->posts} p
                 JOIN {$wpdb->term_relationships} wtr ON wtr.object_id = p.ID
                 JOIN {$wpdb->term_taxonomy} wtt ON wtt.term_taxonomy_id = wtr.term_taxonomy_id
                 WHERE p.post_type='sp_table' AND p.post_status='publish'
                   AND wtt.taxonomy='sp_season' AND wtt.term_id = %d",
                $row->sp_season_id
            ) );

            $archive_url = $row->slug
                ? home_url( '/season/' . $row->slug . '/' )
                : home_url( '/season/' . sanitize_title( $row->season_name ) . '/' );
        ?>
            <a href="<?php echo esc_url( $archive_url ); ?>" class="mlbb-season-card">
                <div class="mlbb-season-card__header">
                    <h3><?php echo esc_html( $row->season_name ); ?></h3>
                    <?php echo $status; ?>
                </div>
                <div class="mlbb-season-card__body">
                    <div class="mlbb-season-card__row">
                        <span class="mlbb-season-card__label">Registration</span>
                        <span class="mlbb-season-card__value"><?php echo esc_html( date( 'M j', $reg_opens ) ); ?> &ndash; <?php echo esc_html( date( 'M j, Y', $reg_closes ) ); ?></span>
                    </div>
                    <div class="mlbb-season-card__row">
                        <span class="mlbb-season-card__label">Play Window</span>
                        <span class="mlbb-season-card__value"><?php echo esc_html( date( 'M j', $play_start ) ); ?> &ndash; <?php echo esc_html( date( 'M j, Y', $play_end ) ); ?></span>
                    </div>
                    <div class="mlbb-season-card__row">
                        <span class="mlbb-season-card__label">Leagues</span>
                        <span class="mlbb-season-card__value"><?php echo $league_count; ?> configured</span>
                    </div>
                </div>
            </a>
        <?php endforeach; ?>
    </div>
    <?php
    return ob_get_clean();
}

function mlbb_all_seasons_css() {
    static $printed = false;
    if ( $printed ) return '';
    $printed = true;
    return '
<style>
.mlbb-all-seasons {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
    gap: 1.2em;
    margin: 1.5em 0;
}
.mlbb-season-card {
    display: block;
    padding: 18px 22px;
    background: rgba(0,0,0,0.35);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 6px;
    color: inherit !important;
    text-decoration: none !important;
    transition: transform 0.15s, border-color 0.15s;
}
.mlbb-season-card:hover {
    transform: translateY(-2px);
    border-color: #00ff5b;
}
.mlbb-season-card__header {
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 14px; gap: 10px;
}
.mlbb-season-card__header h3 {
    margin: 0; color: #fff; font-size: 1.15em; font-weight: 700;
}
.mlbb-season-card__body { display: flex; flex-direction: column; gap: 8px; }
.mlbb-season-card__row {
    display: flex; justify-content: space-between; gap: 10px;
    font-size: 0.85em;
    border-bottom: 1px dashed rgba(255,255,255,0.06);
    padding-bottom: 6px;
}
.mlbb-season-card__row:last-child { border-bottom: none; padding-bottom: 0; }
.mlbb-season-card__label {
    color: rgba(255,255,255,0.5);
    text-transform: uppercase; font-size: 0.72em; letter-spacing: 0.06em;
}
.mlbb-season-card__value { color: rgba(255,255,255,0.9); font-weight: 600; }
</style>';
}


// ── [mlbb_format_leagues] ──────────────────────────────────────────────────
// List franchise pages for all sp_league terms matching a given rule.
// Usage: [mlbb_format_leagues rule="DPBO5"]  or  rule="Brawl" (prefix match)
add_shortcode( 'mlbb_format_leagues', 'mlbb_format_leagues_render' );

function mlbb_format_leagues_render( $atts ) {
    $atts = shortcode_atts( [
        'rule' => '',
    ], $atts );

    if ( $atts['rule'] === '' ) {
        return '<p class="mlbb-league-empty">No rule specified.</p>';
    }

    global $wpdb;

    $leagues = $wpdb->get_results( $wpdb->prepare(
        "SELECT t.term_id, t.name, t.slug, tm.meta_value AS rule
         FROM {$wpdb->terms} t
         JOIN {$wpdb->term_taxonomy} tt ON tt.term_id = t.term_id AND tt.taxonomy = 'sp_league'
         JOIN {$wpdb->termmeta} tm ON tm.term_id = t.term_id AND tm.meta_key = 'mlbb_rule'
         WHERE tm.meta_value LIKE %s
         ORDER BY t.name",
        $wpdb->esc_like( $atts['rule'] ) . '%'
    ) );

    if ( empty( $leagues ) ) {
        return '<p class="mlbb-league-empty">No leagues configured for this format.</p>';
    }

    ob_start();
    echo '<ul class="wp-block-list mlbb-format-leagues">';
    foreach ( $leagues as $lg ) {
        // Find the franchise page by slug
        $page = get_page_by_path( $lg->slug, OBJECT, 'page' );
        $url  = $page ? get_permalink( $page->ID ) : '#';
        echo '<li><a href="' . esc_url( $url ) . '">' . esc_html( $lg->name ) . '</a></li>';
    }
    echo '</ul>';
    return ob_get_clean();
}


// ── [mlbb_signup_commands] ─────────────────────────────────────────────────
// Displays all currently open/scheduled leagues with their exact Discord
// sign-up commands. Auto-injected at the top of /category/sign-ups/.
add_shortcode( 'mlbb_signup_commands', 'mlbb_signup_commands_render' );

function mlbb_signup_commands_render( $atts ) {
    global $wpdb;

    $rows = $wpdb->get_results(
        "SELECT rp.id, rp.entity_id, rp.status, rp.rule, rp.opens_at, rp.closes_at, rp.max_teams,
                p.post_title,
                (SELECT COUNT(*) FROM mlbb_team_registrations tr
                 WHERE tr.period_id = rp.id AND tr.status != 'rejected') AS team_count
         FROM mlbb_registration_periods rp
         JOIN {$wpdb->posts} p ON p.ID = rp.entity_id
         WHERE rp.entity_type = 'league'
           AND p.post_status = 'publish'
           AND rp.status IN ('open','scheduled')
         ORDER BY
             FIELD(rp.status,'open','scheduled'),
             rp.opens_at ASC,
             p.post_title ASC"
    );

    if ( empty( $rows ) ) {
        return '<div class="mlbb-signup-commands"><p class="mlbb-signup-empty">No leagues are currently open or scheduled for registration. Check back soon!</p></div>';
    }

    $rule_labels = [
        'DPBO1'    => 'Draft Pick &middot; BO1',
        'DPBO3'    => 'Draft Pick &middot; BO3',
        'DPBO5'    => 'Draft Pick &middot; BO5',
        'BrawlBO1' => 'Brawl &middot; BO1',
        'BrawlBO3' => 'Brawl &middot; BO3',
        'BrawlBO5' => 'Brawl &middot; BO5',
        'FreePlay' => 'Free Play',
    ];

    $now = current_time( 'timestamp' );

    // Group by status: open first, then scheduled
    $open_rows  = array_filter( $rows, fn( $r ) => $r->status === 'open' );
    $sched_rows = array_filter( $rows, fn( $r ) => $r->status === 'scheduled' );

    ob_start();
    echo mlbb_signup_commands_css();
    ?>
    <div class="mlbb-signup-commands">

        <div class="mlbb-signup-intro">
            <strong>Join the Discord:</strong>
            <a href="https://discord.gg/mlbbna" target="_blank" rel="noopener">discord.gg/mlbbna</a>
            &nbsp;&mdash;&nbsp;then use one of the commands below in any bot channel.
        </div>

        <?php if ( ! empty( $open_rows ) ) : ?>
        <h3 class="mlbb-signup-section-heading">
            <span class="mlbb-badge mlbb-badge--open">Open Now</span>
            Registration Open
        </h3>
        <div class="mlbb-signup-grid">
        <?php foreach ( $open_rows as $row ) :
            $parts       = preg_split( '/\s+(?:—|–|-)+\s+/', $row->post_title, 2 );
            $league_name = $parts[0];
            $season      = isset( $parts[1] ) ? $parts[1] : '';
            $label       = $rule_labels[ $row->rule ] ?? esc_html( $row->rule );
            $is_fp       = ( $row->rule === 'FreePlay' );
            $closes_ts   = $row->closes_at ? strtotime( $row->closes_at ) : null;
            $days_left   = $closes_ts ? max( 0, (int) ceil( ( $closes_ts - $now ) / DAY_IN_SECONDS ) ) : null;
            $cap         = $row->max_teams ? (int) $row->max_teams : null;
            $teams       = (int) $row->team_count;
            $teams_str   = $cap ? $teams . ' / ' . $cap : $teams;
            $cmd_text    = '/league register league_id:' . $row->entity_id;
        ?>
            <div class="mlbb-signup-card mlbb-signup-card--open">
                <div class="mlbb-signup-card__top">
                    <div class="mlbb-signup-card__name"><?php echo esc_html( $league_name ); ?></div>
                    <?php if ( $season ) : ?><div class="mlbb-signup-card__season"><?php echo esc_html( $season ); ?></div><?php endif; ?>
                </div>
                <div class="mlbb-signup-card__meta">
                    <span class="mlbb-signup-card__format"><?php echo $label; ?></span>
                    <?php if ( $cap ) : ?><span class="mlbb-signup-card__teams"><?php echo esc_html( $teams_str ); ?> teams</span><?php endif; ?>
                    <?php if ( $days_left !== null ) : ?><span class="mlbb-signup-card__deadline">Closes in <?php echo $days_left; ?>d</span><?php endif; ?>
                </div>
                <div class="mlbb-signup-card__cmd">
                    <code><?php echo esc_html( $cmd_text ); ?></code>
                    <button class="mlbb-copy-btn" data-copy="<?php echo esc_attr( $cmd_text ); ?>" title="Copy command">&#x2398;</button>
                </div>
                <?php if ( $is_fp ) : ?>
                <div class="mlbb-signup-card__note">
                    <i class="fas fa-info-circle"></i>
                    Solo signup &mdash; the bot randomly assigns you to a team when registration closes.
                </div>
                <?php endif; ?>
            </div>
        <?php endforeach; ?>
        </div>
        <?php endif; ?>

        <?php if ( ! empty( $sched_rows ) ) : ?>
        <h3 class="mlbb-signup-section-heading">
            <span class="mlbb-badge mlbb-badge--scheduled">Coming Soon</span>
            Opening Soon
        </h3>
        <div class="mlbb-signup-grid">
        <?php foreach ( $sched_rows as $row ) :
            $parts       = preg_split( '/\s+(?:—|–|-)+\s+/', $row->post_title, 2 );
            $league_name = $parts[0];
            $season      = isset( $parts[1] ) ? $parts[1] : '';
            $label       = $rule_labels[ $row->rule ] ?? esc_html( $row->rule );
            $is_fp       = ( $row->rule === 'FreePlay' );
            $opens_ts    = $row->opens_at ? strtotime( $row->opens_at ) : null;
            $days_until  = $opens_ts ? max( 0, (int) ceil( ( $opens_ts - $now ) / DAY_IN_SECONDS ) ) : null;
            $cap         = $row->max_teams ? (int) $row->max_teams : null;
            $cmd_text    = '/league register league_id:' . $row->entity_id;
        ?>
            <div class="mlbb-signup-card mlbb-signup-card--scheduled">
                <div class="mlbb-signup-card__top">
                    <div class="mlbb-signup-card__name"><?php echo esc_html( $league_name ); ?></div>
                    <?php if ( $season ) : ?><div class="mlbb-signup-card__season"><?php echo esc_html( $season ); ?></div><?php endif; ?>
                </div>
                <div class="mlbb-signup-card__meta">
                    <span class="mlbb-signup-card__format"><?php echo $label; ?></span>
                    <?php if ( $cap ) : ?><span class="mlbb-signup-card__capacity"><?php echo esc_html( $cap ); ?> team cap</span><?php endif; ?>
                    <?php if ( $days_until !== null ) : ?><span class="mlbb-signup-card__deadline">Opens in <?php echo $days_until; ?>d</span><?php endif; ?>
                </div>
                <div class="mlbb-signup-card__cmd mlbb-signup-card__cmd--preview">
                    <code><?php echo esc_html( $cmd_text ); ?></code>
                    <button class="mlbb-copy-btn" data-copy="<?php echo esc_attr( $cmd_text ); ?>" title="Copy command">&#x2398;</button>
                </div>
                <?php if ( $is_fp ) : ?>
                <div class="mlbb-signup-card__note">
                    <i class="fas fa-info-circle"></i>
                    Solo signup &mdash; the bot randomly assigns you to a team when registration closes.
                </div>
                <?php endif; ?>
            </div>
        <?php endforeach; ?>
        </div>
        <?php endif; ?>

    </div>
    <script>
    (function(){
        document.querySelectorAll('.mlbb-copy-btn').forEach(function(btn){
            btn.addEventListener('click', function(){
                var txt = btn.getAttribute('data-copy');
                if ( navigator.clipboard ) {
                    navigator.clipboard.writeText(txt).then(function(){
                        btn.textContent = '\u2713';
                        setTimeout(function(){ btn.innerHTML = '&#x2398;'; }, 1500);
                    });
                } else {
                    var ta = document.createElement('textarea');
                    ta.value = txt;
                    document.body.appendChild(ta);
                    ta.select();
                    document.execCommand('copy');
                    document.body.removeChild(ta);
                    btn.textContent = '\u2713';
                    setTimeout(function(){ btn.innerHTML = '&#x2398;'; }, 1500);
                }
            });
        });
    })();
    </script>
    <?php
    return ob_get_clean();
}

function mlbb_signup_commands_css() {
    static $printed = false;
    if ( $printed ) return '';
    $printed = true;
    return '
<style>
.mlbb-signup-commands { margin: 1.5em 0; }

.mlbb-signup-intro {
    padding: 14px 18px;
    background: rgba(0,255,91,0.07);
    border: 1px solid rgba(0,255,91,0.2);
    border-radius: 6px;
    margin-bottom: 2em;
    font-size: 0.95em;
    color: rgba(255,255,255,0.8);
}
.mlbb-signup-intro a { color: #00ff5b; font-weight: 700; text-decoration: none; }
.mlbb-signup-intro a:hover { text-decoration: underline; }

.mlbb-signup-section-heading {
    display: flex; align-items: center; gap: 10px;
    margin: 1.5em 0 0.8em;
    color: #fff; font-size: 1em;
    text-transform: uppercase; letter-spacing: 0.06em;
}

.mlbb-signup-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 1em;
    margin-bottom: 1.5em;
}

.mlbb-signup-card {
    padding: 14px 16px;
    background: rgba(0,0,0,0.35);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 6px;
    display: flex; flex-direction: column; gap: 10px;
    transition: border-color 0.15s;
}
.mlbb-signup-card--open     { border-left: 3px solid #1a6b32; }
.mlbb-signup-card--scheduled { border-left: 3px solid #8a7a10; }
.mlbb-signup-card:hover { border-color: rgba(255,255,255,0.2); }

.mlbb-signup-card__name { color: #fff; font-weight: 700; font-size: 1em; }
.mlbb-signup-card__season { color: rgba(255,255,255,0.45); font-size: 0.82em; margin-top: 2px; }

.mlbb-signup-card__meta {
    display: flex; flex-wrap: wrap; gap: 6px; font-size: 0.78em;
}
.mlbb-signup-card__format {
    padding: 2px 7px; background: rgba(0,255,91,0.1);
    color: #00ff5b; border-radius: 3px; font-weight: 600;
}
.mlbb-signup-card__teams,
.mlbb-signup-card__capacity {
    padding: 2px 7px; background: rgba(255,255,255,0.08);
    color: rgba(255,255,255,0.65); border-radius: 3px;
}
.mlbb-signup-card__deadline {
    padding: 2px 7px; background: rgba(255,183,3,0.12);
    color: #FFB703; border-radius: 3px; font-weight: 600;
}

.mlbb-signup-card__cmd {
    display: flex; align-items: center; gap: 8px;
    background: rgba(0,0,0,0.4);
    border: 1px solid rgba(255,255,255,0.1);
    border-radius: 4px; padding: 8px 10px;
}
.mlbb-signup-card__cmd--preview { opacity: 0.55; }
.mlbb-signup-card__cmd code {
    flex: 1;
    background: none; padding: 0;
    color: #00ff5b; font-size: 0.88em;
    font-family: monospace; word-break: break-all;
}

.mlbb-copy-btn {
    background: rgba(255,255,255,0.07);
    border: 1px solid rgba(255,255,255,0.15);
    border-radius: 3px;
    color: rgba(255,255,255,0.6);
    cursor: pointer; font-size: 1em;
    padding: 2px 6px; line-height: 1;
    transition: background 0.1s, color 0.1s;
    flex-shrink: 0;
}
.mlbb-copy-btn:hover { background: rgba(0,255,91,0.15); color: #00ff5b; }

.mlbb-signup-card__note {
    padding: 8px 10px;
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 4px;
    font-size: 0.8em;
    color: rgba(255,255,255,0.55);
}
.mlbb-signup-card__note i { margin-right: 4px; color: #00ff5b; }

.mlbb-signup-empty { color: rgba(255,255,255,0.5); font-style: italic; }
</style>';
}


// ── Auto-inject into /category/sign-ups/ archive ───────────────────────────
add_action( 'loop_start', function( $query ) {
    if ( ! is_admin() && $query->is_main_query() && is_category( 'sign-ups' ) ) {
        static $injected = false;
        if ( $injected ) return;
        $injected = true;
        echo '<div class="mlbb-signup-archive-header container">';
        echo do_shortcode( '[mlbb_signup_commands]' );
        echo '</div>';
    }
}, 5 );
