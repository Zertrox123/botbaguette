import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import db
import profile_ui
import scanner
import war_points


class TestParsersAndDB(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = self.tmp.name
        db.init_db(self.db_path)

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            p = self.db_path + suffix
            if os.path.exists(p):
                os.remove(p)

    def test_parse_amount_variants(self):
        self.assertEqual(db.parse_amount("181032"), 181032)
        self.assertEqual(db.parse_amount("181 032"), 181032)
        self.assertEqual(db.parse_amount("14.4k"), 14400)
        self.assertEqual(db.parse_amount("75,1K"), 75100)
        self.assertEqual(db.parse_amount("2.08m"), 2080000)
        with self.assertRaises(ValueError):
            db.parse_amount("abc")
        with self.assertRaises(ValueError):
            db.parse_amount("-50")

    def test_db_persistence(self):
        p = db.get_or_create_profile(12345, "Plolipop", db_path=self.db_path)
        self.assertEqual(p["pseudo"], "Plolipop")
        self.assertEqual(p["fioles_vertes"], 0)
        self.assertEqual(p["fusion_montures"], 0)
        self.assertEqual(p["fusion_pets"], 0)

        updated = db.update_profile(
            12345,
            {
                "marteaux": 181032,
                "fioles_vertes": 93,
                "fusion_montures": 697,
                "fusion_pets": 949,
            },
            db_path=self.db_path,
        )
        self.assertEqual(updated["marteaux"], 181032)
        self.assertEqual(updated["fioles_vertes"], 93)
        self.assertEqual(updated["fusion_montures"], 697)
        self.assertEqual(updated["fusion_pets"], 949)


class TestProfileUI(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = self.tmp.name
        db.init_db(self.db_path)

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            p = self.db_path + suffix
            if os.path.exists(p):
                os.remove(p)

    def _mock_interaction(self, user_id: int, display_name: str = "Plolipop"):
        inter = MagicMock()
        inter.user = MagicMock()
        inter.user.id = user_id
        inter.user.display_name = display_name
        inter.response = MagicMock()
        inter.response.send_message = AsyncMock()
        inter.response.edit_message = AsyncMock()
        inter.response.send_modal = AsyncMock()
        return inter

    async def test_view_and_modals_end_to_end(self):
        view = profile_ui.ProfileView(1001, emoji_map={}, db_path=self.db_path)
        self.assertEqual(len(view.children), 8)

        # Stranger blocked
        stranger = self._mock_interaction(9999, "Intruder")
        self.assertFalse(await view._check_owner(stranger))
        stranger.response.send_message.assert_awaited_once()

        # Fioles vertes modal
        owner = self._mock_interaction(1001, "Plolipop")
        m1 = profile_ui.AmountModal(view, "fioles_vertes", "Fioles vertes", 0)
        m1.amount_input._value = "93"
        await m1.on_submit(owner)
        owner.response.edit_message.assert_awaited_once()

        # Fusion Montures modal
        owner2 = self._mock_interaction(1001, "Plolipop")
        m2 = profile_ui.AmountModal(view, "fusion_montures", "Fusion Montures", 0)
        m2.amount_input._value = "697"
        await m2.on_submit(owner2)

        # Fusion Pets modal
        owner3 = self._mock_interaction(1001, "Plolipop")
        m3 = profile_ui.AmountModal(view, "fusion_pets", "Fusion Pets", 0)
        m3.amount_input._value = "949"
        await m3.on_submit(owner3)

        p = db.get_or_create_profile(1001, db_path=self.db_path)
        self.assertEqual(p["fioles_vertes"], 93)
        self.assertEqual(p["fusion_montures"], 697)
        self.assertEqual(p["fusion_pets"], 949)

        embed = profile_ui.build_profile_embed(
            p,
            "Plolipop",
            emoji_map={},
            now=datetime(2026, 11, 24, 12, 0, tzinfo=war_points.PARIS_TZ),  # Mardi = J1
        )
        self.assertIn("Fioles vertes", embed.description)
        self.assertEqual(len(embed.fields), 3)
        self.assertIn("Fusion Montures", embed.fields[0].name)
        self.assertIn("697", embed.fields[0].name)
        self.assertIn("Fusion Pets", embed.fields[1].name)
        self.assertIn("949", embed.fields[1].name)
        self.assertEqual(embed.fields[2].name, "Points potentiels")
        self.assertIn("J1 — Mardi", embed.fields[2].value)
        self.assertIn("J2 — Mercredi", embed.fields[2].value)

        # Power strings with b/t suffix must never be parsed as resource amounts
        self.assertIsNone(scanner._extract_amount("752b"))
        self.assertIsNone(scanner._extract_amount("1.25t"))
        self.assertEqual(scanner._extract_amount("75.1k"), 75100)

    async def test_war_points_all_days(self):
        sample = {
            "marteaux": 1000,
            "competences": 400,     # 10 invocs
            "cles_monture": 500,    # 10 invocs
            "oeufs": 200,           # 2 invocs
            "fusion_montures": 10,
            "fusion_pets": 5,
        }
        # J1 Mardi: 1000*5 + 10*225 = 7250
        self.assertEqual(war_points.compute_day_potential(sample, 1)[0], 7250)
        # J2 Mercredi: 10*600 + 10*600 = 12000
        self.assertEqual(war_points.compute_day_potential(sample, 2)[0], 12000)
        # J3 Jeudi: 1000*4 + 10*175 + 2*400 + 5*1250 = 12800
        self.assertEqual(war_points.compute_day_potential(sample, 3)[0], 12800)
        # J4 Vendredi: 10*600 + 10*600 = 12000
        self.assertEqual(war_points.compute_day_potential(sample, 4)[0], 12000)
        # J5 Samedi: 1000*4 + 2*400 + 5*1250 = 11050
        self.assertEqual(war_points.compute_day_potential(sample, 5)[0], 11050)
        # Dimanche (None): 0
        self.assertEqual(war_points.compute_day_potential(sample, None)[0], 0)

        # Lundi (2026-11-23) -> Aujourd'hui Hors guerre, Demain J1 Mardi
        mon = datetime(2026, 11, 23, 12, 0, tzinfo=war_points.PARIS_TZ)
        pair = war_points.get_today_and_tomorrow_potentials(sample, now=mon)
        self.assertIsNone(pair[0]["war_day"])
        self.assertEqual(pair[1]["war_day"], 1)

    async def test_scanner_parse_ocr_items(self):
        def mk(text, cx, cy):
            return {"text": text, "norm": scanner._norm(text), "cx": cx, "cy": cy, "conf": 0.99}

        # Tech tree with merged header (and language-independent: Spanish / German)
        res_tech = scanner.parse_ocr_items([
            mk("75.1kArbolTecnologico", 0.35, 0.10),
            mk("93", 0.20, 0.15),
            mk("57.4%", 0.27, 0.33),
            mk("9.55%", 0.73, 0.33),
        ])
        self.assertEqual(res_tech["screen_type"], "tech_tree")
        self.assertEqual(res_tech["visible"], {"potions": 75100, "fioles_vertes": 93})
        self.assertIn("57.4", res_tech["secret"]["tech_tree_json"])

        # Skills screen in Spanish ('Habilidades 18/18' + 'Invocar x5')
        res_skills = scanner.parse_ocr_items([
            mk("568", 0.19, 0.10),
            mk("Habilidades 18/18", 0.50, 0.10),
            mk("Invocar x5", 0.50, 0.72),
            mk("186", 0.54, 0.75),
        ])
        self.assertEqual(res_skills["screen_type"], "skills")
        self.assertEqual(res_skills["visible"], {"competences": 568})
        self.assertEqual(res_skills["secret"], {"skill_summon_cost": 37.2})

        # Mount merge vs Pet merge in English ('697 Selected' / '949 Selected')
        res_mnt = scanner.parse_ocr_items([
            mk("7511/176086 xp", 0.50, 0.26),
            mk("697 Selected", 0.25, 0.32),
            mk("Upgrade", 0.80, 0.32),
        ])
        self.assertEqual(res_mnt["visible"], {"fusion_montures": 697})

        res_pet = scanner.parse_ocr_items([
            mk("Pets", 0.50, 0.10),
            mk("276197/961824 xp", 0.50, 0.26),
            mk("949 Selected", 0.25, 0.32),
            mk("Upgrade", 0.80, 0.31),
        ])
        self.assertEqual(res_pet["visible"], {"fusion_pets": 949})

        # Main screen in Spanish ('Forja' + 'Nivel 32' + OCR typo '18l032' + power '￥369b')
        res_main = scanner.parse_ocr_items([
            mk("Plolipop", 0.23, 0.09),
            mk("￥369b", 0.24, 0.11),
            mk("Forja", 0.73, 0.76),
            mk("Nivel 32", 0.74, 0.77),
            mk("18l032", 0.51, 0.80),
        ])
        self.assertEqual(res_main["visible"], {"puissance": "369b", "marteaux": 181032})
        self.assertEqual(res_main["secret"]["forge_level"], 32)
        self.assertEqual(res_main["secret"]["forge_avg_j1"], 5.0)
        self.assertEqual(res_main["secret"]["forge_avg_j3"], 4.0)

        # Forge probabilities modal (0..35) with mixed probabilities (50% early, 50% modern -> avg 3.0 / 2.0)
        res_finfo = scanner.parse_ocr_items([
            mk("Level 10", 0.63, 0.25),
            mk("Level 11", 0.86, 0.25),
            mk("50%", 0.67, 0.29), mk("40%", 0.90, 0.29),
            mk("0%",  0.67, 0.33), mk("0%",  0.90, 0.33),
            mk("0%",  0.67, 0.36), mk("0%",  0.90, 0.36),
            mk("50%", 0.67, 0.40), mk("60%", 0.90, 0.40),
        ])
        self.assertEqual(res_finfo["screen_type"], "forge_info")
        self.assertEqual(res_finfo["secret"]["forge_level"], 10)
        self.assertEqual(res_finfo["secret"]["forge_avg_j1"], 3.0)
        self.assertEqual(res_finfo["secret"]["forge_avg_j3"], 2.0)

        # Weighted egg hatch points from real rarity distribution
        avg_hatch = war_points.expected_egg_hatch_points(
            '{"commun": 211, "rare": 309, "epique": 319, "legendaire": 107, "ultime": 3}'
        )
        self.assertAlmostEqual(avg_hatch, 2447.63, places=2)
        self.assertEqual(war_points.forge_level_to_avg_points(0), (2.0, 1.0))
        self.assertEqual(war_points.forge_level_to_avg_points(35), (5.0, 4.0))

        # Animals screen in German (pure timers + '+1', zero French/English keywords!)
        res_de_animals = scanner.parse_ocr_items([
            mk("65", 0.20, 0.10),
            mk("Haustiere", 0.50, 0.10),
            mk("Beschworen x1", 0.50, 0.54),
            mk("100", 0.53, 0.57),
            mk("+1", 0.87, 0.71),
            mk("5h47m", 0.26, 0.76),
        ])
        self.assertEqual(res_de_animals["screen_type"], "animals")
        self.assertEqual(res_de_animals["visible"], {"oeufs": 65})
        self.assertEqual(res_de_animals["secret"], {"egg_summon_cost": 100.0})

        embed_with_power = profile_ui.build_profile_embed(
            {"pseudo": "Plolipop", "puissance": "369b"},
            "Plolipop",
            emoji_map={},
        )
        self.assertEqual(embed_with_power.title, "Plolipop — 369b")

    async def test_scan_correction_modal(self):
        import command as bot_cmd

        view = bot_cmd.ScanConfirmView(1001, {"puissance": "369b", "marteaux": 181032}, {"forge_tier_j1": 5})
        modal = bot_cmd.ScanCorrectionModal(view)
        modal.inputs["puissance"]._value = "400b"
        modal.inputs["marteaux"]._value = "185k"

        inter = self._mock_interaction(1001, "Plolipop")
        await modal.on_submit(inter)
        inter.response.edit_message.assert_awaited_once()
        self.assertEqual(view.visible["puissance"], "400b")
        self.assertEqual(view.visible["marteaux"], 185000)

    async def test_snipe_and_war_embed(self):
        db.update_profile(1, {"pseudo": "MisterArrow", "marteaux": 40000}, db_path=self.db_path)  # 200 000 pts J1
        db.update_profile(2, {"pseudo": "Glad1", "marteaux": 30000}, db_path=self.db_path)        # 150 000 pts J1
        db.update_profile(3, {"pseudo": "Vold", "marteaux": 10000}, db_path=self.db_path)         # 50 000 pts J1

        # Toggle sniper ON then OFF then ON
        self.assertTrue(db.toggle_snipe(2, 1, db_path=self.db_path))
        self.assertFalse(db.toggle_snipe(2, 1, db_path=self.db_path))
        self.assertTrue(db.toggle_snipe(2, 1, db_path=self.db_path))
        self.assertTrue(db.toggle_snipe(3, 1, db_path=self.db_path))

        snipers = db.get_snipers(1, db_path=self.db_path)
        self.assertEqual(snipers, {2, 3})

        embed = profile_ui.build_war_embed(
            1,
            db.get_all_profiles(db_path=self.db_path),
            snipers,
            emoji_map={},
            clan_name="Baguette6",
            deductions={1: 20000},
        )
        self.assertIn("Jour 1 (Mardi)", embed.title)
        self.assertIn("380 000", embed.description)  # Total clan after 20k deduction
        self.assertIn("-20 000 scanné", embed.description)
        self.assertIn("Snipers : 2", embed.description)
        self.assertIn("150 000", embed.description)  # Sniper individual points

        # WarView "Profil" button opens caller's own profile ephemerally
        war_view = profile_ui.WarView({}, db_path=self.db_path)
        inter = self._mock_interaction(2, "Glad1")
        await war_view.open_profile.callback(inter)
        inter.response.send_message.assert_awaited_once()
        kwargs = inter.response.send_message.call_args.kwargs
        self.assertTrue(kwargs.get("ephemeral"))
        self.assertIn("Glad1", kwargs["embed"].title)
        self.assertIn("200 000", embed.description)  # Total snipers (150k + 50k)

    async def test_admin_panel_ascension_filter_deductions_and_member(self):
        import admin_ui
        import ai_assistant

        # Seed player with max_competences=1 and max_montures=0
        p = db.update_profile(
            42,
            {
                "pseudo": "Plolipop",
                "marteaux": 1000,         # 5 000 pts J1
                "competences": 400,       # 10 * 225 = 2 250 pts J1 (MAX!)
                "max_competences": 1,
                "max_forge": 0,
                "include_in_calc": 1,
            },
            db_path=self.db_path,
        )
        sid = p["short_id"]
        self.assertGreaterEqual(sid, 1)

        # Normal mode excludes Max resources (only marteaux = 5 000)
        self.assertEqual(war_points.compute_day_potential(p, 1, ascension_only=False)[0], 5000)
        # Ascension mode counts ONLY Max resources (only competences = 2 250)
        self.assertEqual(war_points.compute_day_potential(p, 1, ascension_only=True)[0], 2250)
        # Resource filter works
        self.assertEqual(war_points.compute_day_potential(p, 1, resource_filter="competences", ascension_only=True)[0], 2250)
        self.assertEqual(war_points.compute_day_potential(p, 1, resource_filter="marteaux", ascension_only=True)[0], 0)

        # War leaderboard OCR + fuzzy match + 10-minute lock
        pairs = scanner.parse_war_leaderboard_items([
            {"text": "Plolipop - 1.5k points", "norm": "plolipop - 1.5k points", "cx": 0.5, "cy": 0.2, "conf": 0.99},
        ])
        self.assertEqual(pairs, [("Plolipop", 1500)])
        matched = scanner.match_player_name("Plolipoq", db.get_all_profiles(db_path=self.db_path))
        self.assertIsNotNone(matched)
        self.assertEqual(matched["user_id"], 42)

        ok1, rem1 = db.record_war_deduction(42, 1, 1500, now=10000, db_path=self.db_path)
        self.assertTrue(ok1)
        self.assertEqual(rem1, 0)
        # Immediate re-scan overwrites without cooldown
        ok2, rem2 = db.record_war_deduction(42, 1, 2000, now=10120, db_path=self.db_path)
        self.assertTrue(ok2)
        self.assertEqual(rem2, 0)
        # Deducted in calculator: 5000 - 2000 = 3000
        deds = db.get_war_deductions(1, db_path=self.db_path)
        self.assertEqual(war_points.compute_day_potential(p, 1, deducted_pts=deds[42])[0], 3000)

        # Admin state persistence & PC embed
        db.update_admin_state(999, {"selected_day": 1, "ascension_only": 1, "resource_filter": "competences"}, db_path=self.db_path)
        panel_embed = admin_ui.build_admin_panel_embed(999, "Baguette6", emoji_map={}, db_path=self.db_path)
        self.assertIn("ASCENSION", panel_embed.description)
        self.assertIn("Plolipop", panel_embed.fields[2].value)

        # Incompatible resource_filter resets to 'all' when switching to another day
        db.update_admin_state(999, {"selected_day": 2, "ascension_only": 0, "resource_filter": "competences"}, db_path=self.db_path)
        panel_embed_j2 = admin_ui.build_admin_panel_embed(999, "Baguette6", emoji_map={}, db_path=self.db_path)
        self.assertIn("**Filtre :** `Tout`", panel_embed_j2.description)
        self.assertEqual(db.get_admin_state(999, db_path=self.db_path)["resource_filter"], "all")

        # Effective Déduits is 0 in Ascension mode when player has 0 ascension points on that day
        db.record_war_deduction(42, 2, 5000, now=10200, db_path=self.db_path)
        db.update_admin_state(999, {"selected_day": 2, "ascension_only": 1, "resource_filter": "all"}, db_path=self.db_path)
        panel_embed_asc = admin_ui.build_admin_panel_embed(999, "Baguette6", emoji_map={}, db_path=self.db_path)
        self.assertNotIn("-5 000", panel_embed_asc.description)

        # New /admin Ascensions (2x2 + filtered, no fire icon) & Œufs/Fusions embeds
        asc_grid = admin_ui.build_ascensions_embed("Baguette6", {}, res_filter="all", db_path=self.db_path)
        self.assertEqual(len(asc_grid.fields), 4)
        self.assertNotIn("🔥", asc_grid.description)
        asc_comp = admin_ui.build_ascensions_embed("Baguette6", {}, res_filter="competences", db_path=self.db_path)
        self.assertIn("Plolipop", asc_comp.fields[0].value)
        eggs_emb = admin_ui.build_eggs_fusions_embed("Baguette6", {}, db_path=self.db_path)
        self.assertEqual(len(eggs_emb.fields), 3)

        # LM Studio /api/v1/chat response parser
        import ai_assistant
        self.assertEqual(
            ai_assistant._extract_reply({
                "output": [
                    {"type": "reasoning", "content": "hidden thoughts"},
                    {"type": "message", "content": "Bonjour clan !"},
                ]
            }),
            "Bonjour clan !",
        )

        # Members page embed + /membre lookup by short_id
        found = db.get_profile_by_identifier(str(sid), db_path=self.db_path)
        self.assertEqual(found["user_id"], 42)
        mem_embed = admin_ui.build_members_page_embed([found], 1, {42}, page=0)
        self.assertIn(f"(id: {sid})", mem_embed.description)
        self.assertIn("🟢", mem_embed.description)

        # AI context builder includes DB snapshot
        ctx = ai_assistant.build_database_context(db_path=self.db_path)
        self.assertIn("Plolipop", ctx)


class TestProfileTextParser(unittest.TestCase):
    def test_real_discord_messages_batch_1(self):
        # Jasnc
        r = scanner.parse_profile_text(
            "Forge ⭐ niv34 4m gold 30k marteaux\n"
            "Animaux ⭐ max 27k\n"
            "Montures ⭐ max 10k\n"
            "Competences ⭐ niv77 22k\n"
            "Fioles 1.96"
        )
        self.assertEqual(r["visible"], {
            "marteaux": 30000,
            "oeufs": 27000,
            "cles_monture": 10000,
            "competences": 22000,
            "fioles_vertes": 1960,
        })
        self.assertEqual(r["secret"]["forge_level"], 34)
        self.assertEqual(r["secret"]["max_forge"], 0)
        self.assertEqual(r["secret"]["max_oeufs"], 1)
        self.assertEqual(r["secret"]["max_montures"], 1)
        self.assertEqual(r["secret"]["max_competences"], 0)

        # LEGANDARIAN
        r = scanner.parse_profile_text(
            "Forge ⭐⭐ niveau 12 / 1,69 M gold et 115k marteaux\n"
            "Animaux ⭐⭐ LVL 50 : 6,5 K coquilles\n"
            "Monture ⭐ LVL 100 : 32,9k\n"
            "Compétences ⭐ LVL 86 : 17,8k\n"
            "Fioles 2,15k"
        )
        self.assertEqual(r["visible"], {
            "marteaux": 115000,
            "oeufs": 6500,
            "cles_monture": 32900,
            "competences": 17800,
            "fioles_vertes": 2150,
        })
        self.assertEqual(r["secret"]["forge_level"], 12)
        self.assertEqual(r["secret"]["forge_avg_j1"], 5.0)
        self.assertEqual(r["secret"]["max_montures"], 1)
        self.assertEqual(r["secret"]["max_oeufs"], 0)

        # CommanderZorg (Animaux has level only, no eggs amount)
        r = scanner.parse_profile_text(
            "Forge ⭐⭐ niveau 14 / 9,69 M gold et 98219 marteaux\n"
            "Animaux ⭐⭐ LVL 86\n"
            "Monture ⭐ LVL 100 : 49,8K - 1978 fusions\n"
            "Compétences ⭐ LVL 100 : 107K\n"
            "Fioles: 2890"
        )
        self.assertEqual(r["visible"], {
            "marteaux": 98219,
            "cles_monture": 49800,
            "fusion_montures": 1978,
            "competences": 107000,
            "fioles_vertes": 2890,
        })
        self.assertNotIn("oeufs", r["visible"])
        self.assertEqual(r["secret"]["max_montures"], 1)
        self.assertEqual(r["secret"]["max_competences"], 1)

        # kojocik (Animaux has fusions only, no eggs)
        r = scanner.parse_profile_text(
            "Animaux⭐⭐ LVL 76 - 113 fusions\n"
            "Monture LVL 100 : 117K - 752 fusions\n"
            "Compétences ⭐ LVL 100 : 82K\n"
            "Fioles: 2670"
        )
        self.assertEqual(r["visible"], {
            "fusion_pets": 113,
            "cles_monture": 117000,
            "fusion_montures": 752,
            "competences": 82000,
            "fioles_vertes": 2670,
        })
        self.assertNotIn("oeufs", r["visible"])

        # PrGx (fusions before number + coquilles d'œufs + 2.510 thousands dot)
        r = scanner.parse_profile_text(
            "Forge ⭐ niveau 32 / 4,71 M gold et 166413 marteaux\n"
            "Animaux ⭐ LVL 100 - fusions 568 - coquilles d'œufs 77,5k\n"
            "Monture ⭐ LVL 98 : 13,7K - 1938 fusions\n"
            "Compétences ⭐ LVL 77 : 46,5K\n"
            "Fioles: 2.510"
        )
        self.assertEqual(r["visible"], {
            "marteaux": 166413,
            "fusion_pets": 568,
            "oeufs": 77500,
            "cles_monture": 13700,
            "fusion_montures": 1938,
            "competences": 46500,
            "fioles_vertes": 2510,
        })
        self.assertEqual(r["secret"]["max_oeufs"], 1)
        self.assertEqual(r["secret"]["max_montures"], 0)

        # Hitaru (space thousands '95 584', '1 905', explicit 0 eggs)
        r = scanner.parse_profile_text(
            "Forge ⭐ niveau 21 / 9,48 M gold et 95 584 marteaux\n"
            "Animaux ⭐⭐ LVL 73 - fusions 244 - coquilles d'œufs 0\n"
            "Monture ⭐ LVL 100 : 37.8K - 1 905 fusions"
        )
        self.assertEqual(r["visible"], {
            "marteaux": 95584,
            "fusion_pets": 244,
            "oeufs": 0,
            "cles_monture": 37800,
            "fusion_montures": 1905,
        })

    def test_real_discord_messages_batch_2_and_chat_guard(self):
        # Sylverte (Clan tech + clefs + space before K)
        r = scanner.parse_profile_text(
            "Monture ⭐ niveau 100 : 55,3 K clefs\n"
            "Compétences ⭐ niveau 77 : 96,3 K tickets\n"
            "Animaux ⭐⭐ niveau 50 : 6,9 K coquilles\n"
            "Clan tech : 2,71 K fioles\n"
            "Forge ⭐⭐ niveau 17 : 158 K marteaux"
        )
        self.assertEqual(r["visible"], {
            "cles_monture": 55300,
            "competences": 96300,
            "oeufs": 6900,
            "fioles_vertes": 2710,
            "marteaux": 158000,
        })

        # Azealion (bold markdown + double colons + Fioles vertes)
        r = scanner.parse_profile_text(
            "**Forge** ⭐⭐ : : lvl 18 / 31.5k Marteaux\n"
            "**Compétences** ⭐ : lvl 100 / 84.7k Tickets\n"
            "**Animaux** ⭐⭐ : : lvl 54 / 17.8k Coquilles\n"
            "**Monture** ⭐ : lvl 100 / 52.8k Clefs\n"
            "**Fioles vertes** : 2.78k"
        )
        self.assertEqual(r["visible"], {
            "marteaux": 31500,
            "competences": 84700,
            "oeufs": 17800,
            "cles_monture": 52800,
            "fioles_vertes": 2780,
        })

        # Silversnake (no space after star + Forge level only)
        r = scanner.parse_profile_text(
            "Monture ⭐ niveau 100 : 43,2 K\n"
            "Compétences ⭐ niveau 100 : 90.8 K\n"
            "Animaux ⭐niveau 100 : 92,1 K\n"
            "Forge ⭐⭐ niveau 8"
        )
        self.assertEqual(r["visible"], {
            "cles_monture": 43200,
            "competences": 90800,
            "oeufs": 92100,
        })
        self.assertNotIn("marteaux", r["visible"])
        self.assertEqual(r["secret"]["forge_level"], 8)

        # Taborlin - Hepta (singular fusion + no stars)
        r = scanner.parse_profile_text(
            "Monture niveau 100 : 17.9K clefs\n"
            "Compétences niveau 77 : 9 K tickets\n"
            "Animaux ⭐niveau 38 : 390 fusion\n"
            "Clan tech : 893 fioles\n"
            "Forge niveau 31 : 113 K marteaux"
        )
        self.assertEqual(r["visible"], {
            "cles_monture": 17900,
            "competences": 9000,
            "fusion_pets": 390,
            "fioles_vertes": 893,
            "marteaux": 113000,
        })
        self.assertNotIn("oeufs", r["visible"])
        self.assertEqual(r["secret"]["forge_level"], 31)

        # Casual chat must not trigger false positives
        chat = scanner.parse_profile_text(
            "Salut tout le monde, merci pour la guerre !\n"
            "Tu as combien de marteaux à la forge ?\n"
            "Je vais farm mes 20k tickets ce soir"
        )
        self.assertEqual(chat, {"visible": {}, "secret": {}})


if __name__ == "__main__":
    unittest.main()
