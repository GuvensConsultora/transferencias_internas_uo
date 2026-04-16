{
    'name': 'Cash Transfer Simple',
    'version': '17.0.2.0.0',
    'summary': 'Transferencia entre diarios de caja/banco con control de dirección por OU',
    'category': 'Accounting',
    'author': 'Guvens Consultora',
    'license': 'LGPL-3',
    'depends': ['account', 'mail', 'operating_unit'],
    'data': [
        'security/cash_transfer_groups.xml',
        'security/ir.model.access.csv',
        'views/cash_transfer_views.xml',
    ],
    'installable': True,
    'application': False,
}
