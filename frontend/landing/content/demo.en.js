  var TENDERS = [
    {
      id: "t1", ref: "1000957926", sector: "defense", match: 0.91,
      title: "Supply of portable tactical communication systems and accessories",
      buyer: "Ministry of Defence — Procurement Directorate",
      deadline: inHours(74), source: "Israeli MoD tender list",
      pegs: {
        participation: [
          "Valid <strong>AS9100D</strong> certification at the submission date",
          "Facility security clearance at level 3 or above",
          "Three comparable projects in the last five years"
        ],
        evaluation: [
          "Technical compliance — <strong>45%</strong>",
          "Price — <strong>35%</strong>",
          "Delivery schedule — <strong>10%</strong>",
          "Local industrial participation — <strong>10%</strong>"
        ],
        guidelines: [
          "480 handheld radios",
          "Temperature range <span dir=\"ltr\">-30°C</span> to <span dir=\"ltr\">+60°C</span>",
          "Minimum reorder quantity: 24 units"
        ],
        standards: ["MIL-STD-810H", "MIL-STD-461G", "IP67"],
        selection: "Highest weighted score · single winner"
      }
    },
    {
      id: "t2", ref: "MUNI-2026-0088", sector: "civil", match: 0.64,
      title: "Framework agreement for municipal water main rehabilitation",
      buyer: "Metropolitan Water Utility",
      deadline: inHours(196), source: "Government publications collection",
      pegs: {
        participation: [
          "Contractor classification <strong>C-4</strong> or above for water and sewage works",
          "<strong>ISO 9001:2015</strong> and <strong>ISO 45001</strong> certification",
          "Professional indemnity insurance of EUR 2,000,000"
        ],
        evaluation: [
          "Price — <strong>60%</strong>",
          "Technical methodology — <strong>25%</strong>",
          "Environmental management plan — <strong>15%</strong>"
        ],
        guidelines: [
          "About 60 km of mains, diameters <span dir=\"ltr\">DN100–DN400</span>",
          "Lining materials approved for contact with drinking water",
          "Agreement term: four years"
        ],
        standards: ["EN 805", "EN ISO 11295"],
        selection: "Most economically advantageous tender · up to three framework winners"
      }
    },
    {
      id: "t3", ref: "NASA-CUAS-2026-11", sector: "defense", match: 0.78,
      title: "Detection and tracking systems for unmanned aircraft",
      buyer: "Airports Security Authority",
      deadline: inHours(19), source: "Government publications collection",
      pegs: {
        participation: [
          "Supplier registered in an EU member state",
          "Security clearance for work on critical infrastructure",
          "<strong>ISO 27001</strong> certification"
        ],
        evaluation: [
          "Detection performance and false-alarm rate — <strong>40%</strong>",
          "Integration with existing command systems — <strong>30%</strong>",
          "Price — <strong>30%</strong>"
        ],
        guidelines: [
          "12 fixed detection stations",
          "3 mobile kits",
          "Detection range of not less than 5 km"
        ],
        standards: ["EN 303 883"],
        selection: "Highest quality score · single winner"
      },
      unclear: "Frequency licensing annex — partially parsed, flagged for human review"
    },
    {
      id: "t4", ref: "JASC-MRO-2026-03", sector: "defense", match: null,
      title: "Maintenance, repair and overhaul services for a helicopter fleet",
      buyer: "Air Support Command",
      deadline: inHours(-52), source: "Israeli MoD tender list",
      needsReview: true
    },
    {
      id: "t5", ref: "HLTH-2026-214", sector: "civil", match: 0.55,
      title: "Supply of monitoring equipment for intensive care units",
      buyer: "Regional Medical Centre",
      deadline: inHours(430), source: "Government publications collection",
      pegs: {
        participation: [
          "Medical device registration in the national device register",
          "Local service representation with a response time of up to 24 hours"
        ],
        evaluation: [
          "Price — <strong>50%</strong>",
          "Technical specification — <strong>30%</strong>",
          "Service and warranty — <strong>20%</strong>"
        ],
        guidelines: [
          "48 multi-parameter monitors",
          "Integration with the existing patient management system"
        ],
        standards: ["IEC 60601-1", "MDR 2017/745"],
        selection: "Highest weighted score"
      }
    }
  ];
