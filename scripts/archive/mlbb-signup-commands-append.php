

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
