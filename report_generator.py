import os
import sqlite3
import json
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
import database

class ReportGenerator:
    @staticmethod
    def generate_assessment_report(session_id):
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                s.id as session_id,
                s.assessment_name,
                s.status,
                s.score,
                s.severity,
                s.risk_level,
                s.safety_escalated,
                s.safety_notes,
                s.started_at,
                s.completed_at,
                p.id as patient_id,
                p.dob,
                p.age,
                p.gender,
                p.phone,
                u.full_name as patient_name,
                u.email as patient_email
            FROM assessment_sessions s
            JOIN patients p ON s.patient_id = p.id
            JOIN users u ON p.user_id = u.id
            WHERE s.id = ?
        """, (session_id,))
        session = cursor.fetchone()
        if not session:
            conn.close()
            raise ValueError(f"Session with ID {session_id} not found.")
        
        cursor.execute("""
            SELECT summary_text, clinician_notes, action_items
            FROM session_summaries
            WHERE session_id = ?
        """, (session_id,))
        summary = cursor.fetchone()
        
        cursor.execute("""
            SELECT question_id, response_value, response_text
            FROM assessment_responses
            WHERE session_id = ?
            ORDER BY id ASC
        """, (session_id,))
        responses = cursor.fetchall()

        cursor.execute("""
            SELECT * FROM transformational_reports WHERE session_id = ?
        """, (session_id,))
        t_report = cursor.fetchone()
        
        conn.close()
        
        os.makedirs("reports", exist_ok=True)
        pdf_path = f"reports/session_{session_id}_report.pdf"
        doc = SimpleDocTemplate(pdf_path, pagesize=letter,
                                rightMargin=40, leftMargin=40,
                                topMargin=40, bottomMargin=40)
        story = []
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'ReportTitle',
            parent=styles['Heading1'],
            fontSize=24,
            leading=28,
            textColor=colors.HexColor('#1E3A8A'),
            spaceAfter=15
        )
        section_style = ParagraphStyle(
            'ReportSection',
            parent=styles['Heading2'],
            fontSize=16,
            leading=20,
            textColor=colors.HexColor('#0F766E'),
            spaceBefore=12,
            spaceAfter=6
        )
        normal_style = ParagraphStyle(
            'ReportNormal',
            parent=styles['Normal'],
            fontSize=10,
            leading=14,
            textColor=colors.HexColor('#374151')
        )
        bold_style = ParagraphStyle(
            'ReportBold',
            parent=normal_style,
            fontName='Helvetica-Bold'
        )
        story.append(Paragraph("CareMinds AI Clinical Assessment Report", title_style))
        story.append(Paragraph(f"Generated on: {session['completed_at'] or session['started_at']}", normal_style))
        story.append(Spacer(1, 10))
        
        info_data = [
            [Paragraph("Patient Name:", bold_style), Paragraph(str(session['patient_name']), normal_style),
             Paragraph("Assessment:", bold_style), Paragraph(str(session['assessment_name']), normal_style)],
            [Paragraph("Date of Birth:", bold_style), Paragraph(str(session['dob']), normal_style),
             Paragraph("Started At:", bold_style), Paragraph(str(session['started_at']), normal_style)],
            [Paragraph("Age / Gender:", bold_style), Paragraph(f"{session['age']} / {session['gender']}", normal_style),
             Paragraph("Completed At:", bold_style), Paragraph(str(session['completed_at'] or 'N/A'), normal_style)],
            [Paragraph("Phone / Email:", bold_style), Paragraph(f"{session['phone']} / {session['patient_email']}", normal_style),
             Paragraph("Status:", bold_style), Paragraph(str(session['status']).upper(), normal_style)]
        ]
        info_table = Table(info_data, colWidths=[90, 170, 90, 170])
        info_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#F3F4F6')),
            ('ALIGN', (0,0), (-1,-1), 'LEFT'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#D1D5DB')),
            ('TOPPADDING', (0,0), (-1,-1), 6),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
            ('LEFTPADDING', (0,0), (-1,-1), 8),
            ('RIGHTPADDING', (0,0), (-1,-1), 8),
        ]))
        story.append(info_table)
        story.append(Spacer(1, 15))
        
        score = session['score'] if session['score'] is not None else 'N/A'
        severity = session['severity'] or 'N/A'
        risk_level = session['risk_level'] or 'low'
        safety_escalated = "YES (🚨 CRITICAL ESCALATION)" if session['safety_escalated'] else "NO"
        
        metrics_data = [
            [Paragraph("Calculated Score", bold_style), Paragraph("Severity Classification", bold_style), Paragraph("Safety Risk Flag", bold_style), Paragraph("Safety Escalated", bold_style)],
            [Paragraph(f"<font size=14 color='#1E3A8A'><b>{score}</b></font>", normal_style),
             Paragraph(f"<b>{severity}</b>", normal_style),
             Paragraph(f"<b>{risk_level.upper()}</b>", normal_style),
             Paragraph(f"<b>{safety_escalated}</b>", normal_style)]
        ]
        metrics_table = Table(metrics_data, colWidths=[130, 150, 120, 120])
        metrics_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#E5E7EB')),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('GRID', (0,0), (-1,-1), 1, colors.HexColor('#9CA3AF')),
            ('TOPPADDING', (0,0), (-1,-1), 8),
            ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ]))
        story.append(metrics_table)
        story.append(Spacer(1, 15))
        
        if session['safety_escalated'] or (session['safety_notes'] and len(session['safety_notes']) > 0):
            story.append(Paragraph("🚨 Safety & Crisis Intervention Notes", section_style))
            safety_notes_text = session['safety_notes'] or "Self-harm / Crisis indicators flagged during session."
            story.append(Paragraph(f"<b>Alert Notes:</b> {safety_notes_text}", ParagraphStyle('SafetyText', parent=normal_style, textColor=colors.HexColor('#991B1B'))))
            story.append(Spacer(1, 10))
        
        if summary:
            story.append(Paragraph("📝 Clinical Summary", section_style))
            story.append(Paragraph(str(summary['summary_text']), normal_style))
            story.append(Spacer(1, 10))
            
            story.append(Paragraph("🩺 Clinician SOAP Notes", section_style))
            soap_lines = str(summary['clinician_notes']).split('\n')
            for line in soap_lines:
                if line.strip():
                    story.append(Paragraph(line, normal_style))
            story.append(Spacer(1, 10))
            
            story.append(Paragraph("📋 Clinical Action Items", section_style))
            action_lines = str(summary['action_items']).split('\n')
            for line in action_lines:
                if line.strip():
                    story.append(Paragraph(line, normal_style))
            story.append(Spacer(1, 15))
        
        if t_report:
            story.append(PageBreak())
            story.append(Paragraph("🌱 Layer 2: Transformational Coaching Report & Insights", section_style))
            story.append(Spacer(1, 10))
            
            t_headers = [
                Paragraph("<b>Dimension</b>", normal_style), Paragraph("<b>Score</b>", normal_style),
                Paragraph("<b>Dimension</b>", normal_style), Paragraph("<b>Score</b>", normal_style)
            ]
            t_rows = [t_headers]
            dimensions_list = [
                ("Emotional Resilience", t_report["emotional_resilience"]),
                ("Growth Mindset", t_report["growth_mindset"]),
                ("Self-Awareness", t_report["self_awareness"]),
                ("Relationship Health", t_report["relationship_health"]),
                ("Personal Agency", t_report["personal_agency"]),
                ("Purpose Alignment", t_report["purpose_alignment"]),
                ("Cognitive Flexibility", t_report["cognitive_flexibility"]),
                ("Future Optimism", t_report["future_optimism"])
            ]
            for i in range(0, len(dimensions_list), 2):
                dim1, val1 = dimensions_list[i]
                dim2, val2 = dimensions_list[i+1]
                t_rows.append([
                    Paragraph(dim1, normal_style), Paragraph(f"<b>{val1}</b>/100", normal_style),
                    Paragraph(dim2, normal_style), Paragraph(f"<b>{val2}</b>/100", normal_style)
                ])
                
            t_scores_table = Table(t_rows, colWidths=[180, 80, 180, 80])
            t_scores_table.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#E5E7EB')),
                ('ALIGN', (0,0), (-1,-1), 'LEFT'),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#9CA3AF')),
                ('TOPPADDING', (0,0), (-1,-1), 5),
                ('BOTTOMPADDING', (0,0), (-1,-1), 5),
            ]))
            story.append(t_scores_table)
            story.append(Spacer(1, 15))
            
            narratives = [
                ("Clinical Risk Summary", t_report["clinical_risk_summary"]),
                ("Deep Narrative Insight", t_report["deep_narrative_insight"]),
                ("Blind Spot Detection", t_report["blind_spot_detection"]),
                ("Strength Recognition", t_report["strength_recognition"]),
                ("AI Coaching Reflection", t_report["coaching_reflection"]),
                ("Personalized Growth Roadmap", t_report["growth_roadmap"])
            ]
            for section_title, content in narratives:
                story.append(Paragraph(f"<b>{section_title}</b>", bold_style))
                story.append(Paragraph(str(content), normal_style))
                story.append(Spacer(1, 8))
            story.append(Spacer(1, 10))
            story.append(PageBreak())

        story.append(Paragraph("📊 Detailed Patient Responses", section_style))
        resp_headers = [Paragraph("<b>Question ID</b>", normal_style), Paragraph("<b>Question Text / Response</b>", normal_style), Paragraph("<b>Value</b>", normal_style)]
        resp_rows = [resp_headers]
        
        scale_key = None
        if 'phq' in session['assessment_name'].lower():
            scale_key = 'phq9'
        elif 'gad' in session['assessment_name'].lower():
            scale_key = 'gad7'
        elif 'who' in session['assessment_name'].lower():
            scale_key = 'who5'
        elif 'pss' in session['assessment_name'].lower() or 'stress' in session['assessment_name'].lower():
            scale_key = 'pss10'
            
        questions_dict = {}
        if scale_key:
            try:
                with open(f"data/assessments/{scale_key}.json", "r", encoding="utf-8") as f_q:
                    import json as j_q
                    scale_data = j_q.load(f_q)
                    for q in scale_data.get("questions", []):
                        questions_dict[q["id"]] = q["text"]
            except Exception as e:
                print(f"Could not load question map: {e}")

        # Load DIRA question texts as well
        try:
            with open("data/assessments/dira.json", "r", encoding="utf-8") as f_dira:
                import json as j_dira
                dira_data = j_dira.load(f_dira)
                for q in dira_data.get("questions", []):
                    questions_dict[q["id"]] = q["text"]
        except Exception as e:
            print(f"Could not load DIRA question map: {e}")
                
        for r in responses:
            q_id = r['question_id']
            q_text = questions_dict.get(q_id, "Question text not found in local scale files.")
            resp_val = r['response_value']
            resp_txt = r['response_text']
            
            resp_rows.append([
                Paragraph(str(q_id), normal_style),
                Paragraph(f"<b>Q:</b> {q_text}<br/><b>A:</b> {resp_txt}", normal_style),
                Paragraph(str(resp_val), normal_style)
            ])
            
        resp_table = Table(resp_rows, colWidths=[60, 410, 50])
        resp_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#F3F4F6')),
            ('ALIGN', (0,0), (0,-1), 'CENTER'),
            ('ALIGN', (2,0), (2,-1), 'CENTER'),
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E5E7EB')),
            ('TOPPADDING', (0,0), (-1,-1), 5),
            ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ]))
        story.append(resp_table)
        doc.build(story)
        print("Clinical PDF Report successfully generated.")
